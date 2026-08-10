"""core/workflows/actions.py — Acciones deterministas para steps de tipo 'action'"""
import logging
from datetime import datetime, timezone, date, timedelta
from typing import Any, Callable, Dict

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
            .select("status, total, due_date")
            .eq("org_id", org_id)
            .execute()
            .data
            or []
        )
        for inv in invoices:
            if inv.get("status") in ("sent", "partial"):
                outstanding += float(inv.get("total") or 0)
                if inv.get("due_date") and inv.get("due_date") < today_str:
                    overdue += float(inv.get("total") or 0)
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


def _gather_business_metrics(org_id: str) -> Dict[str, Any]:
    outstanding, overdue = _sum_outstanding_and_overdue(org_id)
    return {
        "open_deals": _count_open_deals(org_id),
        "outstanding": outstanding,
        "overdue": overdue,
        "low_stock": _count_low_stock(org_id),
        "monthly_net": _sum_monthly_net(org_id),
    }


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
