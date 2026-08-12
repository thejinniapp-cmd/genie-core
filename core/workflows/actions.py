"""core/workflows/actions.py — Acciones deterministas para steps de tipo 'action'"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date, timedelta
from typing import Any, Callable, Dict, List, Optional

from supabase import create_client
import os

log = logging.getLogger("genie.workflow_actions")

ACTIONS: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {}


def register_action(name: str):
    def decorator(fn: Callable[[str, Dict[str, Any]], Dict[str, Any]]):
        ACTIONS[name] = fn
        return fn
    return decorator


from core.workflows.agent_commands import post_command, COMMANDS_BY_AGENT


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
        log.warning(f"[actions] Could not check module {module_key}: {e}")
        return False


def _ai_staff_enabled(org_id: str, staff_key: str) -> bool:
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
        log.warning(f"[actions] Could not check ai staff {staff_key}: {e}")
        return False


AI_STAFF_MODULE_REQUIREMENTS = {
    "sales_agent": "crm",
    "collector_agent": "collections",
    "budget_agent": "control",
}


def _get_invoice_with_contact(org_id: str, invoice_id: str):
    return (
        _db()
        .table("erp_invoices")
        .select("*, crm_contacts(first_name,last_name,email,company_id)")
        .eq("org_id", org_id)
        .eq("id", invoice_id)
        .maybe_single()
        .execute()
        .data
    )


def _create_crm_activity(org_id: str, payload: dict):
    try:
        res = _db().table("crm_activities").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.warning(f"[actions] Could not create CRM activity: {e}")
        return None


@register_action("collections_overdue_activity")
def collections_overdue_activity(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una actividad de cobranza en CRM para una factura vencida.
    Entrada esperada: {"invoice_id": "..."}
    """
    invoice_id = input_data.get("invoice_id")
    if not invoice_id:
        return {"ok": False, "error": "invoice_id missing"}

    invoice = _get_invoice_with_contact(org_id, invoice_id)
    if not invoice:
        return {"ok": False, "error": "invoice not found"}

    contact = invoice.get("crm_contacts") or {}
    contact_id = invoice.get("contact_id")
    company_id = invoice.get("company_id") or contact.get("company_id")
    invoice_number = invoice.get("invoice_number", "S/N")
    total = float(invoice.get("total") or 0)
    due_date = invoice.get("due_date")

    subject = f"Cobrar factura {invoice_number}"
    notes = (
        f"Factura {invoice_number} por ${total:,.2f} vencida el {due_date or 'N/A'}. "
        f"Contactar a {contact.get('first_name') or ''} {contact.get('last_name') or ''} "
        f"({contact.get('email') or 'sin email'})."
    )

    activity = None
    if _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "contact_id": contact_id,
            "company_id": company_id,
            "activity_type": "task",
            "subject": subject,
            "notes": notes,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "status": "pending",
            "metadata": {
                "source": "workflow",
                "workflow_name": "Cobranza vencida",
                "invoice_id": invoice_id,
            },
        })

    return {
        "ok": True,
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("payment_received_ack")
def payment_received_ack(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Registra un agradecimiento y, si hay actividad, genera nota de seguimiento.
    Entrada esperada: {"payment_id": "...", "invoice_id": "..."}
    """
    invoice_id = input_data.get("invoice_id")
    payment_id = input_data.get("payment_id")
    if not invoice_id or not payment_id:
        return {"ok": False, "error": "invoice_id and payment_id required"}

    invoice = _get_invoice_with_contact(org_id, invoice_id)
    if not invoice:
        return {"ok": False, "error": "invoice not found"}

    payments = (
        _db()
        .table("erp_payments")
        .select("*")
        .eq("org_id", org_id)
        .eq("invoice_id", invoice_id)
        .execute()
        .data
        or []
    )
    total_paid = sum(float(p.get("amount") or 0) for p in payments)
    total = float(invoice.get("total") or 0)
    balance = max(total - total_paid, 0)

    contact = invoice.get("crm_contacts") or {}
    contact_id = invoice.get("contact_id")
    company_id = invoice.get("company_id") or contact.get("company_id")
    invoice_number = invoice.get("invoice_number", "S/N")

    subject = f"Pago recibido: {invoice_number}"
    notes = (
        f"Se registró un pago de ${float(input_data.get('amount', 0)):,.2f} "
        f"para la factura {invoice_number}. Saldo restante: ${balance:,.2f}."
    )

    activity = None
    if _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "contact_id": contact_id,
            "company_id": company_id,
            "activity_type": "note",
            "subject": subject,
            "notes": notes,
            "status": "completed",
            "metadata": {
                "source": "workflow",
                "workflow_name": "Pago recibido",
                "invoice_id": invoice_id,
                "payment_id": payment_id,
            },
        })

    return {
        "ok": True,
        "invoice_id": invoice_id,
        "payment_id": payment_id,
        "invoice_number": invoice_number,
        "balance": balance,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("crm_new_contact_followup")
def crm_new_contact_followup(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una actividad de seguimiento para un contacto recién creado.
    Entrada esperada: {"contact_id": "..."}
    """
    contact_id = input_data.get("contact_id")
    if not contact_id:
        return {"ok": False, "error": "contact_id missing"}

    contact = (
        _db()
        .table("crm_contacts")
        .select("*, crm_companies(name)")
        .eq("org_id", org_id)
        .eq("id", contact_id)
        .maybe_single()
        .execute()
        .data
    )
    if not contact:
        return {"ok": False, "error": "contact not found"}

    company = contact.get("crm_companies") or {}
    company_id = contact.get("company_id")
    subject = f"Dar seguimiento a {contact.get('first_name') or ''} {contact.get('last_name') or ''}".strip()
    notes = (
        f"Nuevo contacto de {company.get('name') or 'empresa sin registrar'}. "
        f"Email: {contact.get('email') or 'N/A'}. Teléfono: {contact.get('phone') or 'N/A'}."
    )

    activity = _create_crm_activity(org_id, {
        "org_id": org_id,
        "contact_id": contact_id,
        "company_id": company_id,
        "activity_type": "task",
        "subject": subject,
        "notes": notes,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "status": "pending",
        "metadata": {
            "source": "workflow",
            "workflow_name": "Seguimiento a nuevo contacto",
            "contact_id": contact_id,
        },
    })

    return {
        "ok": True,
        "contact_id": contact_id,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("crm_new_deal_followup")
def crm_new_deal_followup(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una actividad de seguimiento para un deal recién creado.
    Entrada esperada: {"deal_id": "..."}
    """
    deal_id = input_data.get("deal_id")
    if not deal_id:
        return {"ok": False, "error": "deal_id missing"}

    deal = (
        _db()
        .table("crm_deals")
        .select("*, crm_contacts(first_name,last_name,email), crm_companies(name)")
        .eq("org_id", org_id)
        .eq("id", deal_id)
        .maybe_single()
        .execute()
        .data
    )
    if not deal:
        return {"ok": False, "error": "deal not found"}

    contact = deal.get("crm_contacts") or {}
    company = deal.get("crm_companies") or {}
    subject = f"Seguimiento: {deal.get('name', 'Deal')}"
    notes = (
        f"Deal por ${float(deal.get('value') or 0):,.2f}. "
        f"Contacto: {contact.get('first_name') or ''} {contact.get('last_name') or ''} "
        f"({contact.get('email') or 'sin email'}). Empresa: {company.get('name') or 'N/A'}."
    )

    activity = _create_crm_activity(org_id, {
        "org_id": org_id,
        "deal_id": deal_id,
        "contact_id": deal.get("contact_id"),
        "company_id": deal.get("company_id"),
        "activity_type": "task",
        "subject": subject,
        "notes": notes,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        "status": "pending",
        "metadata": {
            "source": "workflow",
            "workflow_name": "Seguimiento a nuevo deal",
            "deal_id": deal_id,
        },
    })

    return {
        "ok": True,
        "deal_id": deal_id,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("crm_stale_deal_nurture")
def crm_stale_deal_nurture(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una actividad para reactivar un deal estancado.
    Entrada esperada: {"deal_id": "..."}
    """
    deal_id = input_data.get("deal_id")
    if not deal_id:
        return {"ok": False, "error": "deal_id missing"}

    deal = (
        _db()
        .table("crm_deals")
        .select("*, crm_contacts(first_name,last_name,email), crm_companies(name)")
        .eq("org_id", org_id)
        .eq("id", deal_id)
        .maybe_single()
        .execute()
        .data
    )
    if not deal:
        return {"ok": False, "error": "deal not found"}

    contact = deal.get("crm_contacts") or {}
    company = deal.get("crm_companies") or {}
    subject = f"Reactivar: {deal.get('name', 'Deal')}"
    notes = (
        f"El deal lleva sin actividad. Valor: ${float(deal.get('value') or 0):,.2f}. "
        f"Contacto: {contact.get('first_name') or ''} {contact.get('last_name') or ''} "
        f"({contact.get('email') or 'sin email'}). Empresa: {company.get('name') or 'N/A'}."
    )

    activity = _create_crm_activity(org_id, {
        "org_id": org_id,
        "deal_id": deal_id,
        "contact_id": deal.get("contact_id"),
        "company_id": deal.get("company_id"),
        "activity_type": "task",
        "subject": subject,
        "notes": notes,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "status": "pending",
        "metadata": {
            "source": "workflow",
            "workflow_name": "Reactivar deals estancados",
            "deal_id": deal_id,
        },
    })

    return {
        "ok": True,
        "deal_id": deal_id,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("inventory_low_stock_po")
def inventory_low_stock_po(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Genera una orden de compra borrador para un artículo con stock bajo.
    Entrada esperada: {"item_id": "..."}
    """
    item_id = input_data.get("item_id")
    if not item_id:
        return {"ok": False, "error": "item_id missing"}

    item = (
        _db()
        .table("inventory_items")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", item_id)
        .maybe_single()
        .execute()
        .data
    )
    if not item:
        return {"ok": False, "error": "item not found"}

    current = float(item.get("current_stock") or 0)
    min_stock = float(item.get("min_stock") or 0)
    reorder_point = float(item.get("reorder_point") or 0)

    if current > min_stock:
        return {"ok": False, "error": "stock is not low", "current_stock": current, "min_stock": min_stock}

    supplier = (
        _db()
        .table("inventory_suppliers")
        .select("*")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .order("created_at")
        .limit(1)
        .execute()
        .data
        or []
    )
    supplier_id = supplier[0]["id"] if supplier else None
    if not supplier_id:
        return {"ok": False, "error": "no active supplier", "item_id": item_id}

    qty = max(reorder_point - current, min_stock - current, 1)
    unit_cost = float(item.get("cost_price") or 0)
    total = round(qty * unit_cost, 2)

    po_data = {
        "org_id": org_id,
        "supplier_id": supplier_id,
        "order_date": date.today().isoformat(),
        "total": total,
        "notes": f"Generada automáticamente por stock bajo: {item.get('name')} (SKU: {item.get('sku')})",
        "status": "draft",
        "metadata": {"source": "workflow", "workflow_name": "Stock bajo - orden de compra", "item_id": item_id},
    }
    po_res = _db().table("inventory_purchase_orders").insert(po_data).execute().data
    if not po_res:
        return {"ok": False, "error": "could not create purchase order"}
    po = po_res[0]

    _db().table("inventory_purchase_order_items").insert({
        "org_id": org_id,
        "order_id": po["id"],
        "item_id": item_id,
        "quantity": qty,
        "unit_cost": unit_cost,
        "total": total,
    }).execute()

    activity = None
    if _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "task",
            "subject": f"Revisar orden de compra para {item.get('name')}",
            "notes": (
                f"Stock actual: {current}, mínimo: {min_stock}. "
                f"Se generó la orden de compra {po['id']} por {qty} unidades."
            ),
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "status": "pending",
            "metadata": {
                "source": "workflow",
                "workflow_name": "Stock bajo - orden de compra",
                "item_id": item_id,
                "purchase_order_id": po["id"],
            },
        })

    return {
        "ok": True,
        "item_id": item_id,
        "purchase_order_id": po["id"],
        "quantity": qty,
        "supplier_id": supplier_id,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("payment_received_accounting")
def payment_received_accounting(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea un ingreso contable automático al recibir un pago.
    Entrada esperada: {"payment_id": "...", "invoice_id": "...", "amount": ...}
    """
    invoice_id = input_data.get("invoice_id")
    payment_id = input_data.get("payment_id")
    amount = float(input_data.get("amount") or 0)
    if not invoice_id or not payment_id:
        return {"ok": False, "error": "invoice_id and payment_id required"}

    if not _module_enabled(org_id, "accounting"):
        return {"ok": False, "error": "accounting module not enabled"}

    invoice = _get_invoice_with_contact(org_id, invoice_id)
    if not invoice:
        return {"ok": False, "error": "invoice not found"}

    payment = (
        _db()
        .table("erp_payments")
        .select("*")
        .eq("org_id", org_id)
        .eq("id", payment_id)
        .maybe_single()
        .execute()
        .data
    )
    if not payment:
        return {"ok": False, "error": "payment not found"}

    income_account = (
        _db()
        .table("erp_accounts")
        .select("*")
        .eq("org_id", org_id)
        .eq("type", "income")
        .eq("is_active", True)
        .order("created_at")
        .limit(1)
        .execute()
        .data
        or []
    )
    if not income_account:
        return {"ok": False, "error": "no income account found"}

    transaction = _db().table("erp_transactions").insert({
        "org_id": org_id,
        "account_id": income_account[0]["id"],
        "type": "income",
        "amount": amount,
        "currency": invoice.get("currency", "MXN"),
        "transaction_date": payment.get("payment_date") or date.today().isoformat(),
        "description": f"Ingreso automático por pago de factura {invoice.get('invoice_number')}",
        "reference": payment_id,
        "contact_id": invoice.get("contact_id"),
        "invoice_id": invoice_id,
        "metadata": {
            "source": "workflow",
            "workflow_name": "Ingreso automático por pago",
            "payment_id": payment_id,
        },
    }).execute().data

    if not transaction:
        return {"ok": False, "error": "could not create accounting transaction"}

    return {
        "ok": True,
        "invoice_id": invoice_id,
        "payment_id": payment_id,
        "transaction_id": transaction[0]["id"],
        "account_id": income_account[0]["id"],
    }


@register_action("accounting_monthly_close")
def accounting_monthly_close(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Crea una tarea de cierre mensual y resumen contable.
    """
    if not _module_enabled(org_id, "accounting"):
        return {"ok": False, "error": "accounting module not enabled"}

    today = date.today()
    start_of_month = today.replace(day=1).isoformat()
    # Primer día del mes siguiente
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    end_of_month = (next_month - timedelta(days=1)).isoformat()

    transactions = (
        _db()
        .table("erp_transactions")
        .select("type, amount")
        .eq("org_id", org_id)
        .gte("transaction_date", start_of_month)
        .lte("transaction_date", end_of_month)
        .execute()
        .data
        or []
    )
    income = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "income")
    expense = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "expense")

    # Asegurar/actualizar período fiscal
    try:
        _db().table("accounting_tax_periods").upsert({
            "org_id": org_id,
            "year": today.year,
            "month": today.month,
            "status": "open",
            "notes": f"Ingresos: ${income:,.2f}, Gastos: ${expense:,.2f}, Neto: ${income - expense:,.2f}",
        }, on_conflict="org_id,year,month").execute()
    except Exception as e:
        log.warning(f"[actions] Could not upsert tax period: {e}")

    activity = None
    if _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "task",
            "subject": f"Revisar cierre mensual contable - {today.strftime('%B %Y')}",
            "notes": (
                f"Resumen del mes: Ingresos ${income:,.2f}, Gastos ${expense:,.2f}, "
                f"Neto ${income - expense:,.2f}. Revisa y cierra el período."
            ),
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "status": "pending",
            "metadata": {
                "source": "workflow",
                "workflow_name": "Cierre mensual contable",
                "year": today.year,
                "month": today.month,
            },
        })

    return {
        "ok": True,
        "year": today.year,
        "month": today.month,
        "income": income,
        "expense": expense,
        "net": income - expense,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


# ── Genie Control: presupuesto, founder dependency, manual autogenerado ───────

def _get_founder(org_id: str) -> Optional[Dict[str, Any]]:
    try:
        rows = (
            _db()
            .table("hr_employees")
            .select("id, name, email")
            .eq("org_id", org_id)
            .eq("is_founder", True)
            .eq("status", "active")
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0] if rows else None
    except Exception as e:
        log.warning(f"[actions] Could not find founder: {e}")
        return None


def _org_run_ids(org_id: str, since_iso: Optional[str] = None) -> List[str]:
    try:
        q = _db().table("workflow_runs").select("id").eq("org_id", org_id)
        if since_iso:
            q = q.gte("started_at", since_iso)
        rows = q.execute().data or []
        return [r["id"] for r in rows]
    except Exception as e:
        log.warning(f"[actions] Could not list org runs: {e}")
        return []


def _run_step_ids(run_ids: List[str]) -> List[str]:
    if not run_ids:
        return []
    try:
        rows = (
            _db()
            .table("workflow_run_steps")
            .select("id")
            .in_("run_id", run_ids)
            .execute()
            .data
            or []
        )
        return [r["id"] for r in rows]
    except Exception as e:
        log.warning(f"[actions] Could not list run steps: {e}")
        return []


@register_action("control_budget_weekly_review")
def control_budget_weekly_review(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compara presupuesto vs. real del mes en curso por cuenta y reporta desviaciones
    por encima del umbral (20% por defecto).
    """
    if not _module_enabled(org_id, "control"):
        return {"ok": False, "error": "control module not enabled"}

    today = date.today()
    threshold_pct = float(input_data.get("threshold_pct", 20))

    budgets = (
        _db()
        .table("erp_budgets")
        .select("account_id, planned_amount, erp_accounts(name, type)")
        .eq("org_id", org_id)
        .eq("year", today.year)
        .eq("month", today.month)
        .execute()
        .data
        or []
    )
    if not budgets:
        return {"ok": True, "deviations": [], "message": "Sin presupuesto cargado para este mes"}

    month_start = today.replace(day=1).isoformat()
    transactions = (
        _db()
        .table("erp_transactions")
        .select("account_id, amount")
        .eq("org_id", org_id)
        .gte("transaction_date", month_start)
        .execute()
        .data
        or []
    )
    actual_by_account: Dict[str, float] = {}
    for t in transactions:
        aid = t.get("account_id")
        if not aid:
            continue
        actual_by_account[aid] = actual_by_account.get(aid, 0.0) + float(t.get("amount") or 0)

    deviations = []
    for b in budgets:
        planned = float(b.get("planned_amount") or 0)
        actual = actual_by_account.get(b["account_id"], 0.0)
        account = b.get("erp_accounts") or {}
        if planned <= 0:
            continue
        variance_pct = round(((actual - planned) / planned) * 100, 1)
        if abs(variance_pct) < threshold_pct:
            continue
        direction = "más" if variance_pct > 0 else "menos"
        narrative = f"{account.get('name', 'Cuenta')} gastó {abs(variance_pct)}% {direction} de lo presupuestado este mes."
        deviations.append({
            "account_id": b["account_id"],
            "account_name": account.get("name"),
            "planned": planned,
            "actual": actual,
            "variance_pct": variance_pct,
            "narrative": narrative,
        })

    activity = None
    if deviations and _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "note",
            "subject": f"Revisión semanal de presupuesto - {today.isoformat()}",
            "notes": "\n".join(d["narrative"] for d in deviations),
            "status": "completed",
            "metadata": {"source": "workflow", "workflow_name": "Revisión semanal de presupuesto"},
        })

    return {
        "ok": True,
        "year": today.year,
        "month": today.month,
        "deviations": deviations,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("control_founder_bottleneck_scan")
def control_founder_bottleneck_scan(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detecta pasos de workflow que resuelve repetidamente el founder y sugiere
    delegar a otro miembro del equipo.
    """
    if not _module_enabled(org_id, "control"):
        return {"ok": False, "error": "control module not enabled"}

    founder = _get_founder(org_id)
    if not founder or not founder.get("email"):
        return {"ok": False, "error": "no founder registered with email"}

    min_occurrences = int(input_data.get("min_occurrences", 3))
    since_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    template_ids = [
        r["id"]
        for r in (
            _db().table("workflow_templates").select("id").eq("org_id", org_id).execute().data or []
        )
    ]
    if not template_ids:
        return {"ok": True, "bottlenecks": []}

    steps = (
        _db()
        .table("workflow_steps")
        .select("id, name, template_id, assigned_to")
        .in_("template_id", template_ids)
        .eq("assigned_to", founder["email"])
        .execute()
        .data
        or []
    )
    if not steps:
        return {"ok": True, "bottlenecks": []}

    step_ids = [s["id"] for s in steps]
    run_steps = (
        _db()
        .table("workflow_run_steps")
        .select("id, step_id, run_id, status, completed_at")
        .in_("step_id", step_ids)
        .eq("status", "completed")
        .gte("completed_at", since_iso)
        .execute()
        .data
        or []
    )

    counts: Dict[str, int] = {}
    for rs in run_steps:
        counts[rs["step_id"]] = counts.get(rs["step_id"], 0) + 1

    delegate = (
        _db()
        .table("hr_employees")
        .select("id, name, email, role_title")
        .eq("org_id", org_id)
        .eq("is_manager", True)
        .eq("is_founder", False)
        .eq("status", "active")
        .limit(1)
        .execute()
        .data
        or []
    )
    delegate_to = delegate[0] if delegate else None

    bottlenecks = []
    for step in steps:
        occurrences = counts.get(step["id"], 0)
        if occurrences < min_occurrences:
            continue
        bottlenecks.append({
            "step_id": step["id"],
            "step_name": step["name"],
            "template_id": step["template_id"],
            "occurrences_last_90d": occurrences,
            "suggested_delegate": delegate_to,
        })

    activity = None
    if bottlenecks and _module_enabled(org_id, "crm"):
        lines = [
            f"'{b['step_name']}' lo resolvió el founder {b['occurrences_last_90d']} veces en 90 días."
            + (f" Sugerencia: delegar a {delegate_to['name']}." if delegate_to else "")
            for b in bottlenecks
        ]
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "task",
            "subject": "Cuellos de botella del founder detectados",
            "notes": "\n".join(lines),
            "status": "pending",
            "metadata": {"source": "workflow", "workflow_name": "Detección de cuello de botella del founder"},
        })

    return {
        "ok": True,
        "bottlenecks": bottlenecks,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


def sync_process_manual(org_id: str, run_id: str) -> None:
    """
    Genera/actualiza el 'manual operativo' del proceso como una fuente RAG,
    a partir del run recién completado. Se llama desde workflow_engine.advance_run
    cuando un run llega a status='completed', para cualquier template (no solo control).
    """
    try:
        runs = _db().table("workflow_runs").select("id, template_id, org_id").eq("id", run_id).limit(1).execute().data or []
        if not runs:
            return
        run = runs[0]
        templates = _db().table("workflow_templates").select("id, name, description").eq("id", run["template_id"]).limit(1).execute().data or []
        if not templates:
            return
        template = templates[0]

        run_steps = (
            _db()
            .table("workflow_run_steps")
            .select("id, step_id, status, completed_at, workflow_steps(name, step_order, assigned_to, type)")
            .eq("run_id", run_id)
            .execute()
            .data
            or []
        )
        run_steps.sort(key=lambda rs: (rs.get("workflow_steps") or {}).get("step_order", 0))

        lines = [f"# Manual: {template['name']}", ""]
        if template.get("description"):
            lines.append(template["description"])
            lines.append("")
        lines.append("Pasos del proceso, tal como se ejecutó la última vez:")
        for rs in run_steps:
            step = rs.get("workflow_steps") or {}
            responsable = step.get("assigned_to") or ("agente IA" if step.get("type") == "agent" else "sin asignar")
            lines.append(f"{step.get('step_order', '?')}. {step.get('name', 'Paso')} — responsable: {responsable} ({rs.get('status')})")
        content = "\n".join(lines)

        existing_rows = (
            _db()
            .table("rag_sources")
            .select("id")
            .eq("org_id", org_id)
            .eq("metadata->>template_id", template["id"])
            .eq("source_type", "process_manual")
            .limit(1)
            .execute()
            .data
            or []
        )
        existing = existing_rows[0] if existing_rows else None
        payload = {
            "org_id": org_id,
            "name": f"Manual: {template['name']}",
            "content": content,
            "source_type": "process_manual",
            "scope": "global",
            "always_include": False,
            "metadata": {"template_id": template["id"], "run_id": run_id, "auto_generated": True},
        }
        if existing:
            _db().table("rag_sources").update(payload).eq("id", existing["id"]).execute()
        else:
            _db().table("rag_sources").insert(payload).execute()
    except Exception as e:
        log.warning(f"[actions] Could not sync process manual for run {run_id}: {e}")


# ── Genie Caja: forecast de 13 semanas, concentración, cobranza proactiva ─────

def _commitment_occurrences_in_range(commitment: Dict[str, Any], week_start: date, week_end: date) -> int:
    freq = commitment.get("frequency", "monthly")
    if freq == "weekly":
        return 1
    if freq == "biweekly":
        return 1 if (week_start.isocalendar()[1] % 2 == 0) else 0
    # monthly
    day = min(max(int(commitment.get("day_of_month") or 1), 1), 28)
    count = 0
    d = week_start
    while d <= week_end:
        if d.day == day:
            count += 1
        d += timedelta(days=1)
    return count


def compute_cash_forecast(org_id: str, weeks: int = 13) -> Dict[str, Any]:
    """
    Proyección de flujo de efectivo semana a semana.
    Entradas: saldo de cuentas por cobrar por fecha de vencimiento. Salidas/entradas:
    compromisos recurrentes activos. Punto de partida: último checkpoint manual de saldo.
    """
    today = date.today()

    snapshot_rows = (
        _db()
        .table("erp_cash_balance_snapshots")
        .select("balance, as_of_date")
        .eq("org_id", org_id)
        .order("as_of_date", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    starting_balance = float(snapshot_rows[0]["balance"]) if snapshot_rows else 0.0

    week_starts = [today + timedelta(days=7 * i) for i in range(weeks)]

    invoices = (
        _db()
        .table("erp_invoices")
        .select("id, total, due_date")
        .eq("org_id", org_id)
        .in_("status", ["sent", "partial"])
        .execute()
        .data
        or []
    )
    invoice_ids = [inv["id"] for inv in invoices]
    paid_by_invoice: Dict[str, float] = {}
    if invoice_ids:
        payment_rows = (
            _db()
            .table("erp_payments")
            .select("invoice_id, amount")
            .eq("org_id", org_id)
            .in_("invoice_id", invoice_ids)
            .execute()
            .data
            or []
        )
        for p in payment_rows:
            paid_by_invoice[p["invoice_id"]] = paid_by_invoice.get(p["invoice_id"], 0.0) + float(p.get("amount") or 0)

    inflow_by_week = [0.0] * weeks
    for inv in invoices:
        balance = max(float(inv.get("total") or 0) - paid_by_invoice.get(inv["id"], 0.0), 0)
        if balance <= 0 or not inv.get("due_date"):
            continue
        due = date.fromisoformat(inv["due_date"])
        if due < today:
            inflow_by_week[0] += balance
            continue
        week_idx = (due - today).days // 7
        if week_idx < weeks:
            inflow_by_week[week_idx] += balance

    commitments = (
        _db()
        .table("erp_recurring_commitments")
        .select("type, amount, frequency, day_of_month, weekday")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )
    outflow_by_week = [0.0] * weeks
    for i, ws in enumerate(week_starts):
        we = ws + timedelta(days=6)
        for c in commitments:
            occurrences = _commitment_occurrences_in_range(c, ws, we)
            if occurrences <= 0:
                continue
            amount = float(c.get("amount") or 0) * occurrences
            if c.get("type") == "expense":
                outflow_by_week[i] += amount
            else:
                inflow_by_week[i] += amount

    weeks_out = []
    running = starting_balance
    first_negative_week = None
    for i, ws in enumerate(week_starts):
        running = running + inflow_by_week[i] - outflow_by_week[i]
        weeks_out.append({
            "week_start": ws.isoformat(),
            "projected_inflow": round(inflow_by_week[i], 2),
            "projected_outflow": round(outflow_by_week[i], 2),
            "running_balance": round(running, 2),
        })
        if running < 0 and first_negative_week is None:
            first_negative_week = i

    runway_weeks = first_negative_week if first_negative_week is not None else weeks

    return {
        "starting_balance": starting_balance,
        "weeks": weeks_out,
        "first_negative_week": first_negative_week,
        "runway_weeks": runway_weeks,
    }


def compute_customer_concentration(org_id: str, months: int = 12, threshold_pct: float = 30.0) -> Dict[str, Any]:
    """Concentración de ingresos por cliente (empresa, o contacto si no tiene empresa)."""
    since = (date.today() - timedelta(days=30 * months)).isoformat()
    invoices = (
        _db()
        .table("erp_invoices")
        .select("total, company_id, contact_id, issue_date")
        .eq("org_id", org_id)
        .gte("issue_date", since)
        .execute()
        .data
        or []
    )
    by_customer: Dict[str, float] = {}
    for inv in invoices:
        key = inv.get("company_id") or inv.get("contact_id")
        if not key:
            continue
        by_customer[key] = by_customer.get(key, 0.0) + float(inv.get("total") or 0)

    total = sum(by_customer.values())
    ranked = sorted(by_customer.items(), key=lambda kv: kv[1], reverse=True)

    top1_pct = round((ranked[0][1] / total) * 100, 1) if ranked and total > 0 else 0.0
    top2_pct = round((sum(v for _, v in ranked[:2]) / total) * 100, 1) if total > 0 else 0.0

    return {
        "window_months": months,
        "total_revenue": round(total, 2),
        "top1_customer_id": ranked[0][0] if ranked else None,
        "top1_pct": top1_pct,
        "top2_combined_pct": top2_pct,
        "at_risk": top1_pct > threshold_pct or top2_pct > threshold_pct,
        "threshold_pct": threshold_pct,
    }


def compute_days_to_collect(org_id: str, limit: int = 50) -> Optional[float]:
    """Promedio de días entre emisión de factura y último pago, para facturas pagadas recientes."""
    invoices = (
        _db()
        .table("erp_invoices")
        .select("id, issue_date")
        .eq("org_id", org_id)
        .eq("status", "paid")
        .order("issue_date", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    if not invoices:
        return None
    invoice_ids = [inv["id"] for inv in invoices]
    payments = (
        _db()
        .table("erp_payments")
        .select("invoice_id, payment_date")
        .eq("org_id", org_id)
        .in_("invoice_id", invoice_ids)
        .execute()
        .data
        or []
    )
    last_payment_by_invoice: Dict[str, str] = {}
    for p in payments:
        pid = p["invoice_id"]
        pdate = p.get("payment_date")
        if not pdate:
            continue
        if pid not in last_payment_by_invoice or pdate > last_payment_by_invoice[pid]:
            last_payment_by_invoice[pid] = pdate

    days = []
    for inv in invoices:
        pdate = last_payment_by_invoice.get(inv["id"])
        if not pdate or not inv.get("issue_date"):
            continue
        delta = (date.fromisoformat(pdate) - date.fromisoformat(inv["issue_date"])).days
        if delta >= 0:
            days.append(delta)

    if not days:
        return None
    return round(sum(days) / len(days), 1)


def suggest_credit_instrument(forecast: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Heurística: sugiere factoraje si el faltante se explica por timing de cobros, si no línea revolvente."""
    if forecast.get("first_negative_week") is None:
        return None
    idx = forecast["first_negative_week"]
    weeks_data = forecast.get("weeks") or []
    if idx >= len(weeks_data):
        return None
    shortfall = abs(weeks_data[idx]["running_balance"])
    future_inflow = sum(w["projected_inflow"] for w in weeks_data[idx + 1:])
    if future_inflow >= shortfall:
        return {
            "instrument": "factoraje",
            "reason": f"Hay ${future_inflow:,.2f} en cobros proyectados después de la semana {idx + 1} que podrían adelantarse para cubrir el faltante de ${shortfall:,.2f}.",
        }
    return {
        "instrument": "línea de crédito revolvente",
        "reason": f"El déficit proyectado de ${shortfall:,.2f} en la semana {idx + 1} no se explica solo por timing de cobros — conviene una línea de crédito para el componente estructural.",
    }


@register_action("cash_weekly_forecast")
def cash_weekly_forecast(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Proyección semanal de flujo de caja + concentración de clientes, con nota si hay riesgo."""
    if not _module_enabled(org_id, "collections"):
        return {"ok": False, "error": "collections module not enabled"}

    forecast = compute_cash_forecast(org_id)
    concentration = compute_customer_concentration(org_id)

    findings = []
    if forecast.get("first_negative_week") is not None:
        findings.append(
            f"El saldo proyectado se vuelve negativo en la semana {forecast['first_negative_week'] + 1} "
            f"(runway de {forecast['runway_weeks']} semanas)."
        )
    if concentration.get("at_risk"):
        findings.append(
            f"Concentración de clientes en riesgo: el cliente principal representa {concentration['top1_pct']}% "
            f"de los ingresos de los últimos {concentration['window_months']} meses."
        )

    activity = None
    if findings and _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "note",
            "subject": "Proyección semanal de flujo de caja",
            "notes": "\n".join(findings),
            "status": "completed",
            "metadata": {"source": "workflow", "workflow_name": "Proyección semanal de flujo de caja"},
        })

    return {
        "ok": True,
        "runway_weeks": forecast["runway_weeks"],
        "concentration": concentration,
        "findings": findings,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


# ── Genie Cumple: calendario fiscal, CFDI, simulador de régimen ───────────────

_CFDI_NS = {
    "cfdi": "http://www.sat.gob.mx/cfd/4",
    "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
}


def parse_cfdi_xml(xml_string: str) -> Dict[str, Any]:
    """Parsea un CFDI 4.0 pegado como texto. Lanza ValueError si no es un CFDI válido/timbrado."""
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        raise ValueError(f"XML inválido: {e}")

    if not root.tag.endswith("Comprobante"):
        raise ValueError("El XML no es un Comprobante CFDI (nodo raíz inesperado)")

    emisor = root.find("cfdi:Emisor", _CFDI_NS)
    receptor = root.find("cfdi:Receptor", _CFDI_NS)
    timbre = root.find(".//tfd:TimbreFiscalDigital", _CFDI_NS)

    if timbre is None:
        raise ValueError("El CFDI no tiene TimbreFiscalDigital — no está timbrado por el SAT")
    uuid_fiscal = timbre.get("UUID")
    if not uuid_fiscal:
        raise ValueError("El TimbreFiscalDigital no trae UUID")

    fecha_raw = root.get("Fecha", "")
    fecha_emision = fecha_raw.split("T")[0] if fecha_raw else None

    iva = 0.0
    for traslado in root.findall(".//cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado", _CFDI_NS):
        try:
            iva += float(traslado.get("Importe") or 0)
        except ValueError:
            pass

    return {
        "uuid_fiscal": uuid_fiscal,
        "tipo_comprobante": root.get("TipoDeComprobante"),
        "rfc_emisor": emisor.get("Rfc") if emisor is not None else None,
        "nombre_emisor": emisor.get("Nombre") if emisor is not None else None,
        "rfc_receptor": receptor.get("Rfc") if receptor is not None else None,
        "nombre_receptor": receptor.get("Nombre") if receptor is not None else None,
        "fecha_emision": fecha_emision,
        "subtotal": float(root.get("SubTotal") or 0),
        "iva": round(iva, 2),
        "total": float(root.get("Total") or 0),
        "moneda": root.get("Moneda", "MXN"),
    }


def import_cfdi(org_id: str, xml_string: str) -> Dict[str, Any]:
    """Parsea un CFDI y lo guarda (upsert por uuid_fiscal — reimportar no duplica)."""
    if not _module_enabled(org_id, "cumple"):
        return {"ok": False, "error": "cumple module not enabled"}
    try:
        parsed = parse_cfdi_xml(xml_string)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    payload = {"org_id": org_id, **parsed, "source": "xml_paste", "raw_xml": xml_string}
    try:
        res = _db().table("cfdi_documents").upsert(payload, on_conflict="org_id,uuid_fiscal").execute()
    except Exception as e:
        return {"ok": False, "error": f"could not store CFDI: {e}"}
    return {"ok": True, "cfdi": res.data[0] if res.data else None}


def audit_cfdi(org_id: str, months: int = 3) -> Dict[str, Any]:
    """Hallazgos de auditoría sobre los CFDI importados: RFC no corresponde, proveedor irregular, discrepancia."""
    since = (date.today() - timedelta(days=30 * months)).isoformat()

    profile_rows = (
        _db().table("fiscal_profile").select("rfc").eq("org_id", org_id).limit(1).execute().data or []
    )
    org_rfc = profile_rows[0]["rfc"] if profile_rows else None

    docs = (
        _db()
        .table("cfdi_documents")
        .select("id, uuid_fiscal, rfc_receptor, rfc_emisor, nombre_emisor, tipo_comprobante, total, status, is_efos_flagged, fecha_emision")
        .eq("org_id", org_id)
        .gte("fecha_emision", since)
        .execute()
        .data
        or []
    )

    findings = []
    if org_rfc:
        for d in docs:
            if d.get("rfc_receptor") and d["rfc_receptor"] != org_rfc:
                findings.append({
                    "type": "rfc_mismatch",
                    "cfdi_id": d["id"],
                    "uuid_fiscal": d["uuid_fiscal"],
                    "message": f"El CFDI {d['uuid_fiscal']} tiene como receptor {d['rfc_receptor']}, no coincide con el RFC de la organización ({org_rfc}).",
                })

    for d in docs:
        if d.get("is_efos_flagged"):
            findings.append({
                "type": "efos_flagged",
                "cfdi_id": d["id"],
                "uuid_fiscal": d["uuid_fiscal"],
                "message": f"El proveedor {d.get('nombre_emisor') or d.get('rfc_emisor')} está marcado como irregular ante el SAT.",
            })

    cfdi_income_total = sum(
        float(d.get("total") or 0) for d in docs if d.get("tipo_comprobante") == "I" and d.get("status") == "vigente"
    )
    transactions = (
        _db()
        .table("erp_transactions")
        .select("type, amount")
        .eq("org_id", org_id)
        .gte("transaction_date", since)
        .execute()
        .data
        or []
    )
    booked_income = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "income")

    discrepancy_pct = None
    if booked_income > 0:
        discrepancy_pct = round(abs(cfdi_income_total - booked_income) / booked_income * 100, 1)
        if discrepancy_pct > 15:
            findings.append({
                "type": "income_discrepancy",
                "message": (
                    f"Los CFDI de ingreso vigentes suman ${cfdi_income_total:,.2f} pero la contabilidad registra "
                    f"${booked_income:,.2f} en el mismo período ({discrepancy_pct}% de diferencia)."
                ),
            })

    return {
        "window_months": months,
        "documents_reviewed": len(docs),
        "cfdi_income_total": round(cfdi_income_total, 2),
        "booked_income": round(booked_income, 2),
        "discrepancy_pct": discrepancy_pct,
        "findings": findings,
    }


def _period_due_date(year: int, month: int, due_day: int, months_offset: int) -> date:
    target_month = month + months_offset
    target_year = year
    while target_month > 12:
        target_month -= 12
        target_year += 1
    while target_month < 1:
        target_month += 12
        target_year -= 1
    return date(target_year, target_month, min(due_day, 28))


def _period_income_expense(org_id: str, year: int, month: int) -> tuple:
    start = date(year, month, 1).isoformat()
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)).isoformat()
    txs = (
        _db()
        .table("erp_transactions")
        .select("type, amount")
        .eq("org_id", org_id)
        .gte("transaction_date", start)
        .lt("transaction_date", end)
        .execute()
        .data
        or []
    )
    income = sum(float(t.get("amount") or 0) for t in txs if t.get("type") == "income")
    expense = sum(float(t.get("amount") or 0) for t in txs if t.get("type") == "expense")
    return income, expense


def _upsert_obligation_instance(
    org_id: str, obligation: Dict[str, Any], year: int, month: int, due_date: date, isr_rate_pct: float
) -> Optional[Dict[str, Any]]:
    draft_amount = None
    obligation_type = obligation.get("obligation_type")
    if obligation_type == "iva_mensual":
        income, expense = _period_income_expense(org_id, year, month)
        draft_amount = round(income * 0.16 - expense * 0.16, 2)
    elif obligation_type == "isr_mensual" and isr_rate_pct > 0:
        income, expense = _period_income_expense(org_id, year, month)
        draft_amount = round(max(income - expense, 0) * (isr_rate_pct / 100), 2)

    try:
        res = _db().table("fiscal_obligation_instances").upsert({
            "org_id": org_id,
            "obligation_id": obligation["id"],
            "year": year,
            "month": month,
            "due_date": due_date.isoformat(),
            "draft_amount": draft_amount,
        }, on_conflict="org_id,obligation_id,year,month").execute()
        return res.data[0] if res.data else None
    except Exception as e:
        log.warning(f"[actions] Could not upsert fiscal obligation instance: {e}")
        return None


def generate_fiscal_calendar(org_id: str, months_ahead: int = 3) -> Dict[str, Any]:
    """Asegura las instancias de obligaciones fiscales de los próximos N meses, con borrador numérico simple."""
    if not _module_enabled(org_id, "cumple"):
        return {"ok": False, "error": "cumple module not enabled"}

    obligations = (
        _db().table("fiscal_obligations").select("*").eq("org_id", org_id).eq("is_active", True).execute().data or []
    )
    profile_rows = _db().table("fiscal_profile").select("metadata").eq("org_id", org_id).limit(1).execute().data or []
    isr_rate_pct = float((profile_rows[0].get("metadata") or {}).get("isr_rate_pct", 0)) if profile_rows else 0.0

    today = date.today()
    periods = []
    y, m = today.year, today.month
    for _ in range(months_ahead):
        periods.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    instances = []
    for obligation in obligations:
        if obligation.get("frequency") == "annual":
            due_month = obligation.get("due_month") or 3
            due_day = min(int(obligation.get("due_day") or 31), 28)
            for yr in {today.year, today.year + 1}:
                due_date = date(yr, due_month, due_day)
                if due_date < today:
                    continue
                inst = _upsert_obligation_instance(org_id, obligation, yr, due_month, due_date, isr_rate_pct)
                if inst:
                    instances.append(inst)
            continue

        for (yr, mo) in periods:
            due_date = _period_due_date(yr, mo, int(obligation.get("due_day") or 17), int(obligation.get("months_offset") or 1))
            inst = _upsert_obligation_instance(org_id, obligation, yr, mo, due_date, isr_rate_pct)
            if inst:
                instances.append(inst)

    return {"ok": True, "instances": instances}


def simulate_tax_regime(org_id: str, regimes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compara la carga fiscal estimada entre regímenes usando tasas que aporta el usuario (no tablas oficiales)."""
    since = (date.today() - timedelta(days=365)).isoformat()
    txs = (
        _db()
        .table("erp_transactions")
        .select("type, amount")
        .eq("org_id", org_id)
        .gte("transaction_date", since)
        .execute()
        .data
        or []
    )
    income = sum(float(t.get("amount") or 0) for t in txs if t.get("type") == "income")
    expense = sum(float(t.get("amount") or 0) for t in txs if t.get("type") == "expense")
    net_income = max(income - expense, 0)

    results = []
    for r in regimes:
        rate = float(r.get("estimated_rate_pct") or 0)
        estimated_tax = round(net_income * (rate / 100), 2)
        results.append({
            "name": r.get("name", "Régimen"),
            "estimated_rate_pct": rate,
            "estimated_tax": estimated_tax,
            "net_after_tax": round(net_income - estimated_tax, 2),
        })
    results.sort(key=lambda r: r["estimated_tax"])

    return {
        "window_days": 365,
        "net_income": round(net_income, 2),
        "regimes": results,
        "disclaimer": "Estimación comparativa con tasas que tú proporcionas — no es una declaración ni asesoría fiscal formal.",
    }


def get_inventory_fiscal_impact(org_id: str) -> Dict[str, Any]:
    """Compara el valor de inventario contra las compras a proveedores registradas este mes."""
    items = (
        _db()
        .table("inventory_items")
        .select("current_stock, cost_price")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )
    inventory_value = sum(float(i.get("current_stock") or 0) * float(i.get("cost_price") or 0) for i in items)

    month_start = date.today().replace(day=1).isoformat()
    txs = (
        _db()
        .table("erp_transactions")
        .select("amount, erp_accounts(name)")
        .eq("org_id", org_id)
        .eq("type", "expense")
        .gte("transaction_date", month_start)
        .execute()
        .data
        or []
    )
    purchases_this_month = sum(
        float(t.get("amount") or 0) for t in txs if (t.get("erp_accounts") or {}).get("name") == "Proveedores"
    )

    flag = inventory_value > 0 and purchases_this_month == 0
    return {
        "inventory_value": round(inventory_value, 2),
        "purchases_booked_this_month": round(purchases_this_month, 2),
        "flag": flag,
        "message": (
            "Hay valor de inventario pero no se registraron compras a proveedores este mes — revisa si falta capturar gastos."
            if flag else None
        ),
    }


@register_action("cumple_weekly_check")
def cumple_weekly_check(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Revisión semanal: calendario fiscal próximo a vencer + auditoría de CFDI."""
    if not _module_enabled(org_id, "cumple"):
        return {"ok": False, "error": "cumple module not enabled"}

    calendar = generate_fiscal_calendar(org_id)
    audit = audit_cfdi(org_id)

    soon_cutoff = (date.today() + timedelta(days=7)).isoformat()
    due_soon = [
        i for i in (calendar.get("instances") or [])
        if i.get("status") == "pending" and i.get("due_date") and i["due_date"] <= soon_cutoff
    ]

    findings = [f"Obligación fiscal vence el {i['due_date']} (aún pendiente)." for i in due_soon]
    findings.extend(f["message"] for f in audit.get("findings", []))

    activity = None
    if findings and _module_enabled(org_id, "crm"):
        activity = _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "note",
            "subject": "Revisión semanal de cumplimiento",
            "notes": "\n".join(findings),
            "status": "completed",
            "metadata": {"source": "workflow", "workflow_name": "Revisión semanal de cumplimiento"},
        })

    return {
        "ok": True,
        "due_soon_count": len(due_soon),
        "audit_findings_count": len(audit.get("findings", [])),
        "findings": findings,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


# ── Helpers para métricas del Chief of Staff ─────────────────────────────────

def _count_open_deals(org_id: str) -> int:
    try:
        return (
            _db()
            .table("crm_deals")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .eq("status", "open")
            .execute()
            .count
            or 0
        )
    except Exception as e:
        log.warning(f"[actions] Could not count deals: {e}")
        return 0


def _sum_outstanding_and_overdue(org_id: str) -> tuple:
    today_str = date.today().isoformat()
    outstanding = 0.0
    overdue = 0.0
    try:
        invoices = (
            _db()
            .table("erp_invoices")
            .select("id, status, total, due_date")
            .eq("org_id", org_id)
            .execute()
            .data
            or []
        )
        invoice_ids = [inv["id"] for inv in invoices if inv.get("status") in ("sent", "partial")]
        payments: Dict[str, float] = {}
        if invoice_ids:
            rows = (
                _db()
                .table("erp_payments")
                .select("invoice_id, amount")
                .eq("org_id", org_id)
                .in_("invoice_id", invoice_ids)
                .execute()
                .data
                or []
            )
            for r in rows:
                payments[r["invoice_id"]] = payments.get(r["invoice_id"], 0.0) + float(r.get("amount") or 0)

        for inv in invoices:
            if inv.get("status") not in ("sent", "partial"):
                continue
            total = float(inv.get("total") or 0)
            balance = max(total - payments.get(inv["id"], 0.0), 0)
            if balance <= 0:
                continue
            outstanding += balance
            if inv.get("due_date") and inv.get("due_date") < today_str:
                overdue += balance
    except Exception as e:
        log.warning(f"[actions] Could not summarize invoices: {e}")
    return outstanding, overdue


def _count_low_stock(org_id: str) -> int:
    try:
        return (
            _db()
            .table("inventory_items")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .eq("is_active", True)
            .lte("current_stock", "min_stock")
            .execute()
            .count
            or 0
        )
    except Exception as e:
        log.warning(f"[actions] Could not count low stock: {e}")
        return 0


def _sum_monthly_net(org_id: str) -> float:
    if not _module_enabled(org_id, "accounting"):
        return 0.0
    try:
        month_start = date.today().replace(day=1).isoformat()
        txs = (
            _db()
            .table("erp_transactions")
            .select("type, amount")
            .eq("org_id", org_id)
            .gte("transaction_date", month_start)
            .execute()
            .data
            or []
        )
        income = sum(float(t.get("amount") or 0) for t in txs if t.get("type") == "income")
        expense = sum(float(t.get("amount") or 0) for t in txs if t.get("type") == "expense")
        return income - expense
    except Exception as e:
        log.warning(f"[actions] Could not summarize accounting: {e}")
        return 0.0


def _sum_budget_variance_pct(org_id: str) -> float:
    """Mayor desviación % absoluta de presupuesto vs. real del mes en curso."""
    if not _module_enabled(org_id, "control"):
        return 0.0
    try:
        result = control_budget_weekly_review(org_id, {"threshold_pct": 0})
        deviations = result.get("deviations") or []
        if not deviations:
            return 0.0
        return max(abs(d["variance_pct"]) for d in deviations)
    except Exception as e:
        log.warning(f"[actions] Could not compute budget variance: {e}")
        return 0.0


def _sum_founder_dependency_pct(org_id: str) -> float:
    """% de tareas humanas completadas en los últimos 30 días que resolvió el founder."""
    if not _module_enabled(org_id, "control"):
        return 0.0
    try:
        founder = _get_founder(org_id)
        if not founder or not founder.get("email"):
            return 0.0
        since_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        step_ids = _run_step_ids(_org_run_ids(org_id, since_iso))
        if not step_ids:
            return 0.0
        tasks = (
            _db()
            .table("workflow_run_tasks")
            .select("assigned_to, type, status")
            .in_("run_step_id", step_ids)
            .in_("status", ["approved", "completed"])
            .eq("type", "human_internal")
            .execute()
            .data
            or []
        )
        if not tasks:
            return 0.0
        founder_tasks = sum(1 for t in tasks if t.get("assigned_to") == founder["email"])
        return round((founder_tasks / len(tasks)) * 100, 1)
    except Exception as e:
        log.warning(f"[actions] Could not compute founder dependency: {e}")
        return 0.0


def _sum_runway_weeks(org_id: str) -> float:
    try:
        return float(compute_cash_forecast(org_id).get("runway_weeks") or 0)
    except Exception as e:
        log.warning(f"[actions] Could not compute runway: {e}")
        return 0.0


def _sum_customer_concentration_pct(org_id: str) -> float:
    try:
        return float(compute_customer_concentration(org_id).get("top1_pct") or 0)
    except Exception as e:
        log.warning(f"[actions] Could not compute concentration: {e}")
        return 0.0


def _count_fiscal_obligations_due_soon(org_id: str) -> float:
    try:
        soon_cutoff = (date.today() + timedelta(days=7)).isoformat()
        res = (
            _db()
            .table("fiscal_obligation_instances")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .eq("status", "pending")
            .lte("due_date", soon_cutoff)
            .execute()
        )
        return float(res.count or 0)
    except Exception as e:
        log.warning(f"[actions] Could not count fiscal obligations due soon: {e}")
        return 0.0


def _count_cfdi_audit_findings(org_id: str) -> float:
    try:
        return float(len(audit_cfdi(org_id).get("findings") or []))
    except Exception as e:
        log.warning(f"[actions] Could not count CFDI audit findings: {e}")
        return 0.0


def _gather_business_metrics(org_id: str) -> Dict[str, Any]:
    outstanding, overdue = _sum_outstanding_and_overdue(org_id)
    metrics = {
        "open_deals": _count_open_deals(org_id),
        "outstanding": outstanding,
        "overdue": overdue,
        "low_stock": _count_low_stock(org_id),
        "monthly_net": _sum_monthly_net(org_id),
        "budget_variance_pct": _sum_budget_variance_pct(org_id),
        "founder_dependency_pct": _sum_founder_dependency_pct(org_id),
    }
    # Las métricas condicionales solo se incluyen si su módulo está activo, para no
    # disparar falsas alertas con los defaults en orgs que no tienen el módulo.
    if _module_enabled(org_id, "collections"):
        metrics["runway_weeks"] = _sum_runway_weeks(org_id)
        metrics["customer_concentration_pct"] = _sum_customer_concentration_pct(org_id)
    if _module_enabled(org_id, "cumple"):
        metrics["fiscal_obligations_due_soon"] = _count_fiscal_obligations_due_soon(org_id)
        metrics["cfdi_audit_findings_count"] = _count_cfdi_audit_findings(org_id)
    return metrics


def get_business_metrics(org_id: str) -> Dict[str, Any]:
    """Public wrapper for Chief of Staff business metrics."""
    return _gather_business_metrics(org_id)


def _find_stream_id(org_id: str, input_stream_id: Optional[str] = None) -> Optional[str]:
    if input_stream_id:
        return input_stream_id
    try:
        rows = (
            _db()
            .table("streams")
            .select("id")
            .eq("org_id", org_id)
            .order("created_at")
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0]["id"] if rows else None
    except Exception as e:
        log.warning(f"[actions] Could not find stream: {e}")
        return None


def _send_summary_message(
    org_id: str,
    stream_id: Optional[str],
    summary_text: str,
    workflow_name: str,
) -> Optional[str]:
    if not stream_id:
        return None
    try:
        msg = _db().table("messages").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "role": "assistant",
            "author": "chief_of_staff",
            "content": {"text": summary_text},
            "metadata": {"source": "workflow", "workflow_name": workflow_name},
        }).execute().data
        return msg[0]["id"] if msg else None
    except Exception as e:
        log.warning(f"[actions] Could not insert summary message: {e}")
        return None


def _create_summary_activity(
    org_id: str,
    summary_text: str,
    stream_id: Optional[str],
    message_id: Optional[str],
    workflow_name: str,
):
    if not _module_enabled(org_id, "crm"):
        return None
    try:
        return _create_crm_activity(org_id, {
            "org_id": org_id,
            "activity_type": "note",
            "subject": workflow_name,
            "notes": summary_text,
            "status": "completed",
            "metadata": {
                "source": "workflow",
                "workflow_name": workflow_name,
                "stream_id": stream_id,
                "message_id": message_id,
            },
        })
    except Exception as e:
        log.warning(f"[actions] Could not create summary activity: {e}")
        return None


@register_action("chief_of_staff_daily_summary")
def chief_of_staff_daily_summary(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Genera y envía un resumen ejecutivo diario al stream principal.
    """
    metrics = _gather_business_metrics(org_id)
    summary_text = (
        f"Buen día. Resumen ejecutivo: "
        f"{metrics['open_deals']} deals abiertos, "
        f"${metrics['outstanding']:,.2f} por cobrar (${metrics['overdue']:,.2f} vencido), "
        f"{metrics['low_stock']} artículos con stock bajo, "
        f"neto mensual ${metrics['monthly_net']:,.2f}."
    )

    stream_id = _find_stream_id(org_id, input_data.get("stream_id"))
    message_id = _send_summary_message(org_id, stream_id, summary_text, "Resumen ejecutivo diario")
    activity = _create_summary_activity(org_id, summary_text, stream_id, message_id, "Resumen ejecutivo diario")

    return {
        "ok": True,
        "summary": summary_text,
        "stream_id": stream_id,
        "message_id": message_id,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
    }


@register_action("chief_of_staff_orchestrate")
def chief_of_staff_orchestrate(org_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Chief of Staff revisa el negocio, publica el resumen y emite órdenes
    (comandos) para los agentes de cada módulo habilitado.
    """
    metrics = _gather_business_metrics(org_id)
    summary_text = (
        f"Buen día. Resumen ejecutivo: "
        f"{metrics['open_deals']} deals abiertos, "
        f"${metrics['outstanding']:,.2f} por cobrar (${metrics['overdue']:,.2f} vencido), "
        f"{metrics['low_stock']} artículos con stock bajo, "
        f"neto mensual ${metrics['monthly_net']:,.2f}."
    )

    stream_id = _find_stream_id(org_id, input_data.get("stream_id"))
    message_id = _send_summary_message(org_id, stream_id, summary_text, "Orquestación diaria del Chief of Staff")
    activity = _create_summary_activity(org_id, summary_text, stream_id, message_id, "Orquestación diaria del Chief of Staff")

    # Determinar qué agentes están activos y emitir comandos
    today = date.today()
    if today.month == 12:
        last_day_of_month = today.replace(day=31)
    else:
        last_day_of_month = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    force_accounting = bool(input_data.get("force_accounting"))

    commands = []
    for agent_key, command in COMMANDS_BY_AGENT.items():
        # AI Staff: requiere que el staff y su módulo asociado estén habilitados
        if agent_key in AI_STAFF_MODULE_REQUIREMENTS:
            required_module = AI_STAFF_MODULE_REQUIREMENTS[agent_key]
            if not _ai_staff_enabled(org_id, agent_key) or not _module_enabled(org_id, required_module):
                continue
        else:
            if not _module_enabled(org_id, agent_key):
                continue
            if agent_key == "accounting" and not (today == last_day_of_month or force_accounting):
                continue
        cmd_msg = post_command(
            org_id=org_id,
            agent_key=agent_key,
            command=command,
            stream_id=stream_id,
            payload={"metrics": metrics, "summary": summary_text},
            source="chief_of_staff",
        )
        if cmd_msg:
            commands.append({
                "agent_key": agent_key,
                "command": command,
                "message_id": cmd_msg["id"],
            })

    return {
        "ok": True,
        "summary": summary_text,
        "stream_id": stream_id,
        "message_id": message_id,
        "activity_created": activity is not None,
        "activity_id": activity.get("id") if activity else None,
        "commands": commands,
    }


def run_action(org_id: str, action_name: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
    if action_name not in ACTIONS:
        return {"ok": False, "error": f"Action '{action_name}' not registered"}
    try:
        return ACTIONS[action_name](org_id, input_data)
    except Exception as e:
        log.error(f"[actions] Error running '{action_name}': {e}", exc_info=True)
        return {"ok": False, "error": str(e)}
