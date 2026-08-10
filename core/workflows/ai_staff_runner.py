"""core/workflows/ai_staff_runner.py — Ejecutores de los AI Staff especializados.

Esta lógica vive en el core para que pueda ser invocada tanto desde la API
como desde el scheduler de workflows cuando el Chief of Staff delega tareas.
"""
import os
import logging
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List

from supabase import create_client

log = logging.getLogger("genie.ai_staff_runner")


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


def run_collector_agent(org_id: str) -> Dict[str, Any]:
    """Revisa facturas vencidas y crea tareas de cobranza en CRM."""
    if not _staff_enabled(org_id, "collector_agent"):
        return {"ok": False, "reason": "collector_agent not enabled"}
    if not _module_enabled(org_id, "collections"):
        return {"ok": False, "reason": "collections module not enabled"}

    today = date.today().isoformat()
    invoices = (
        _db()
        .table("erp_invoices")
        .select("id, invoice_number, total, due_date, status, contact_id, crm_contacts(first_name,last_name)")
        .eq("org_id", org_id)
        .execute()
        .data
        or []
    )

    overdue = []
    for inv in invoices:
        if inv.get("status") == "overdue" or (
            inv.get("status") in ("sent", "partial")
            and inv.get("due_date")
            and inv.get("due_date") < today
        ):
            overdue.append(inv)

    created = 0
    actions = []
    for inv in overdue:
        contact = inv.get("crm_contacts") or {}
        subject = f"Cobrar factura {inv['invoice_number']}"
        notes = (
            f"El Agente Cobrador detectó que la factura {inv['invoice_number']} "
            f"por ${float(inv['total']):,.2f} está vencida. "
            f"Contactar a {contact.get('first_name') or ''} {contact.get('last_name') or ''}."
        )
        try:
            if _module_enabled(org_id, "crm"):
                _db().table("crm_activities").insert({
                    "org_id": org_id,
                    "contact_id": inv.get("contact_id"),
                    "activity_type": "task",
                    "subject": subject,
                    "notes": notes,
                    "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                    "status": "pending",
                    "metadata": {"source": "ai_staff", "staff_key": "collector_agent", "invoice_id": inv["id"]},
                }).execute()
            created += 1
            actions.append({"invoice_id": inv["id"], "invoice_number": inv["invoice_number"], "total": inv["total"], "subject": subject})
        except Exception as e:
            actions.append({"invoice_id": inv["id"], "error": str(e)})

    return {
        "staff_key": "collector_agent",
        "overdue_invoices": len(overdue),
        "actions_created": created,
        "actions": actions,
    }


AI_STAFF_RUNNERS = {
    "sales_agent": run_sales_agent,
    "collector_agent": run_collector_agent,
}


def run_ai_staff_command(org_id: str, staff_key: str, command: str) -> Dict[str, Any]:
    """Ejecuta un comando de AI Staff (usado por el command bus del Chief)."""
    runner = AI_STAFF_RUNNERS.get(staff_key)
    if not runner:
        return {"ok": False, "error": f"No runner for {staff_key}"}
    log.info(f"[ai_staff_runner] Executing {staff_key}/{command} for org {org_id}")
    return runner(org_id)
