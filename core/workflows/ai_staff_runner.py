"""core/workflows/ai_staff_runner.py — Ejecutores de los AI Staff especializados.

Esta lógica vive en el core para que pueda ser invocada tanto desde la API
como desde el scheduler de workflows cuando el Chief of Staff delega tareas.
"""
import os
import logging
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List

from supabase import create_client

from core.workflows.actions import control_budget_weekly_review, cumple_weekly_check
from core.connectors.executor import execute_connector_action

log = logging.getLogger("genie.ai_staff_runner")

REMINDER_TEMPLATES = {
    1: {
        "subject": "Recordatorio: factura {invoice_number} pendiente de pago",
        "body": (
            "Hola {first_name},\n\n"
            "Te escribimos para recordarte que la factura {invoice_number} por ${total:,.2f} "
            "está pendiente de pago desde el {due_date}. Si ya la pagaste, ignora este mensaje.\n\n"
            "Saludos."
        ),
        "telegram": "Hola {first_name}, la factura {invoice_number} por ${total:,.2f} está pendiente de pago. ¿Nos ayudas a confirmarla?",
    },
    2: {
        "subject": "Factura {invoice_number} vencida hace más de una semana",
        "body": (
            "Hola {first_name},\n\n"
            "La factura {invoice_number} por ${total:,.2f}, con vencimiento el {due_date}, "
            "sigue pendiente. Te pedimos regularizarla a la brevedad para evitar interrupciones en el servicio.\n\n"
            "Quedamos atentos."
        ),
        "telegram": "Aviso importante: la factura {invoice_number} (${total:,.2f}) lleva más de una semana vencida. Por favor regulariza tu pago.",
    },
}


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _module_enabled(org_id: str, module_key: str) -> bool:
    try:
        row = (
            _db()
            .table("organization_modules")
            .select("enabled")
            .eq("organization_id", org_id)
            .eq("module_key", module_key)
            .maybe_single()
            .execute()
            .data
        )
        return bool(row and row.get("enabled"))
    except Exception as e:
        log.warning(f"[ai_staff_runner] Could not check module {module_key}: {e}")
        return False


def _staff_enabled(org_id: str, staff_key: str) -> bool:
    try:
        row = (
            _db()
            .table("organization_ai_staff")
            .select("enabled")
            .eq("organization_id", org_id)
            .eq("staff_key", staff_key)
            .maybe_single()
            .execute()
            .data
        )
        return bool(row and row.get("enabled"))
    except Exception as e:
        log.warning(f"[ai_staff_runner] Could not check ai staff {staff_key}: {e}")
        return False


def run_sales_agent(org_id: str) -> Dict[str, Any]:
    """Revisa deals abiertos y crea tareas de seguimiento en CRM."""
    if not _staff_enabled(org_id, "sales_agent"):
        return {"ok": False, "reason": "sales_agent not enabled"}
    if not _module_enabled(org_id, "crm"):
        return {"ok": False, "reason": "crm module not enabled"}

    deals = (
        _db()
        .table("crm_deals")
        .select("id, name, value, contact_id, company_id, crm_contacts(first_name,last_name), crm_companies(name)")
        .eq("org_id", org_id)
        .eq("status", "open")
        .execute()
        .data
        or []
    )

    created = 0
    actions = []
    for deal in deals:
        contact = deal.get("crm_contacts") or {}
        company = deal.get("crm_companies") or {}
        subject = f"Seguimiento sugerido: {deal['name']}"
        notes = (
            f"El Agente de Ventas detectó que el deal '{deal['name']}' está abierto. "
            f"Sugerir seguimiento con {contact.get('first_name') or ''} {contact.get('last_name') or ''} "
            f"({company.get('name') or 'sin empresa'})."
        )
        try:
            _db().table("crm_activities").insert({
                "org_id": org_id,
                "deal_id": deal["id"],
                "contact_id": deal.get("contact_id"),
                "company_id": deal.get("company_id"),
                "activity_type": "task",
                "subject": subject,
                "notes": notes,
                "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                "status": "pending",
                "metadata": {"source": "ai_staff", "staff_key": "sales_agent"},
            }).execute()
            created += 1
            actions.append({"deal_id": deal["id"], "deal_name": deal["name"], "subject": subject})
        except Exception as e:
            actions.append({"deal_id": deal["id"], "error": str(e)})

    return {"staff_key": "sales_agent", "actions_created": created, "actions": actions}


def _reminder_tier_for_days(days_overdue: int) -> int:
    if days_overdue >= 15:
        return 3
    if days_overdue >= 8:
        return 2
    if days_overdue >= 1:
        return 1
    return 0


def _send_invoice_reminder(org_id: str, invoice: Dict[str, Any], contact: Dict[str, Any], tier: int) -> Dict[str, Any]:
    template = REMINDER_TEMPLATES.get(tier)
    if not template:
        return {"email_sent": False, "telegram_sent": False}

    ctx = {
        "first_name": contact.get("first_name") or "",
        "invoice_number": invoice.get("invoice_number", "S/N"),
        "total": float(invoice.get("total") or 0),
        "due_date": invoice.get("due_date") or "N/A",
    }

    email_sent = False
    email = contact.get("email")
    if email:
        try:
            execute_connector_action(org_id, "gmail", "send_email", {
                "to": email,
                "subject": template["subject"].format(**ctx),
                "body": template["body"].format(**ctx),
            })
            email_sent = True
        except Exception as e:
            log.warning(f"[ai_staff_runner] Could not send email reminder for invoice {invoice.get('id')}: {e}")

    telegram_sent = False
    chat_id = (contact.get("metadata") or {}).get("telegram_chat_id")
    if chat_id:
        try:
            execute_connector_action(org_id, "telegram", "send_message", {
                "chat_id": chat_id,
                "text": template["telegram"].format(**ctx),
            })
            telegram_sent = True
        except Exception as e:
            log.warning(f"[ai_staff_runner] Could not send telegram reminder for invoice {invoice.get('id')}: {e}")

    return {"email_sent": email_sent, "telegram_sent": telegram_sent}


def run_collector_agent(org_id: str) -> Dict[str, Any]:
    """
    Revisa facturas vencidas y ejecuta la secuencia de cobranza:
    tier 1 (1-7 días) y tier 2 (8-14 días) envían recordatorio real por email
    (y Telegram si el contacto tiene chat_id guardado); tier 3 (15+ días)
    escala a una tarea humana en CRM en vez de seguir insistiendo por bot.
    """
    if not _staff_enabled(org_id, "collector_agent"):
        return {"ok": False, "reason": "collector_agent not enabled"}
    if not _module_enabled(org_id, "collections"):
        return {"ok": False, "reason": "collections module not enabled"}

    today = date.today()
    invoices = (
        _db()
        .table("erp_invoices")
        .select("id, invoice_number, total, due_date, status, contact_id, metadata, crm_contacts(first_name,last_name,email,metadata)")
        .eq("org_id", org_id)
        .execute()
        .data
        or []
    )

    overdue = [
        inv for inv in invoices
        if inv.get("status") in ("sent", "partial") and inv.get("due_date") and inv["due_date"] < today.isoformat()
    ]

    created = 0
    actions = []
    for inv in overdue:
        contact = inv.get("crm_contacts") or {}
        days_overdue = (today - date.fromisoformat(inv["due_date"])).days
        target_tier = _reminder_tier_for_days(days_overdue)
        current_tier = int((inv.get("metadata") or {}).get("reminder_tier") or 0)

        if target_tier <= current_tier:
            continue  # ya se contactó en este tier (o uno mayor)

        result_entry = {
            "invoice_id": inv["id"],
            "invoice_number": inv["invoice_number"],
            "total": inv["total"],
            "days_overdue": days_overdue,
            "tier": target_tier,
        }

        if target_tier >= 3:
            subject = f"Llamar a cliente: cobranza automática no funcionó — factura {inv['invoice_number']}"
            notes = (
                f"La factura {inv['invoice_number']} por ${float(inv['total']):,.2f} lleva {days_overdue} días vencida "
                f"y no hubo respuesta a los recordatorios automáticos. Contactar a "
                f"{contact.get('first_name') or ''} {contact.get('last_name') or ''} por teléfono."
            )
            if _module_enabled(org_id, "crm"):
                try:
                    _db().table("crm_activities").insert({
                        "org_id": org_id,
                        "contact_id": inv.get("contact_id"),
                        "activity_type": "task",
                        "subject": subject,
                        "notes": notes,
                        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                        "status": "pending",
                        "metadata": {"source": "ai_staff", "staff_key": "collector_agent", "invoice_id": inv["id"], "escalated": True},
                    }).execute()
                except Exception as e:
                    log.warning(f"[ai_staff_runner] Could not create escalation activity for invoice {inv['id']}: {e}")
            result_entry["escalated"] = True
        else:
            result_entry.update(_send_invoice_reminder(org_id, inv, contact, target_tier))

        try:
            new_metadata = {
                **(inv.get("metadata") or {}),
                "reminder_tier": target_tier,
                "last_reminder_at": datetime.now(timezone.utc).isoformat(),
            }
            _db().table("erp_invoices").update({"metadata": new_metadata}).eq("id", inv["id"]).execute()
        except Exception as e:
            log.warning(f"[ai_staff_runner] Could not update reminder state for invoice {inv['id']}: {e}")

        created += 1
        actions.append(result_entry)

    return {
        "staff_key": "collector_agent",
        "overdue_invoices": len(overdue),
        "actions_created": created,
        "actions": actions,
    }


def run_budget_agent(org_id: str) -> Dict[str, Any]:
    """Compara presupuesto vs. real del mes en curso y reporta desviaciones."""
    if not _staff_enabled(org_id, "budget_agent"):
        return {"ok": False, "reason": "budget_agent not enabled"}
    if not _module_enabled(org_id, "control"):
        return {"ok": False, "reason": "control module not enabled"}

    result = control_budget_weekly_review(org_id, {})
    deviations = result.get("deviations") or []

    return {
        "staff_key": "budget_agent",
        "actions_created": len(deviations),
        "actions": deviations,
    }


def run_compliance_agent(org_id: str) -> Dict[str, Any]:
    """Revisa el calendario fiscal y audita los CFDI importados."""
    if not _staff_enabled(org_id, "compliance_agent"):
        return {"ok": False, "reason": "compliance_agent not enabled"}
    if not _module_enabled(org_id, "cumple"):
        return {"ok": False, "reason": "cumple module not enabled"}

    result = cumple_weekly_check(org_id, {})

    return {
        "staff_key": "compliance_agent",
        "actions_created": result.get("due_soon_count", 0) + result.get("audit_findings_count", 0),
        "actions": result.get("findings", []),
    }


AI_STAFF_RUNNERS = {
    "sales_agent": run_sales_agent,
    "collector_agent": run_collector_agent,
    "budget_agent": run_budget_agent,
    "compliance_agent": run_compliance_agent,
}


def run_ai_staff_command(org_id: str, staff_key: str, command: str) -> Dict[str, Any]:
    """Ejecuta un comando de AI Staff (usado por el command bus del Chief)."""
    runner = AI_STAFF_RUNNERS.get(staff_key)
    if not runner:
        return {"ok": False, "error": f"No runner for {staff_key}"}
    log.info(f"[ai_staff_runner] Executing {staff_key}/{command} for org {org_id}")
    return runner(org_id)
