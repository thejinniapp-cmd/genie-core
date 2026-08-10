"""api/routes/ai_staff.py — AI Staff: agentes de ventas y cobrador."""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import os
from datetime import datetime, timezone, date, timedelta
from supabase import create_client

from core.workflows.scheduler import tick_chief_of_staff
from core.workflows.actions import get_business_metrics
from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _staff_enabled(org_id: str, staff_key: str) -> bool:
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


def _module_enabled(org_id: str, module_key: str) -> bool:
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


@router.get("/")
def list_ai_staff(org_id: str = Depends(get_current_org)):
    rows = (
        _db()
        .table("organization_ai_staff")
        .select("*, marketplace_items(*)")
        .eq("organization_id", org_id)
        .execute()
        .data
        or []
    )
    # Si no hay registros, devolver los items oficiales con enabled=false por defecto
    if not rows:
        items = _db().table("marketplace_items").select("*").eq("item_type", "ai_staff").eq("is_public", True).execute().data or []
        return [
            {
                "staff_key": item["slug"],
                "enabled": False,
                "marketplace_items": item,
            }
            for item in items
        ]
    return rows


@router.post("/{staff_key}/run")
def run_ai_staff(staff_key: str, org_id: str = Depends(get_current_org)):
    if not _staff_enabled(org_id, staff_key):
        raise HTTPException(403, f"El AI Staff '{staff_key}' no está activado")

    db = _db()

    if staff_key == "sales_agent":
        if not _module_enabled(org_id, "crm"):
            raise HTTPException(403, "El agente de ventas requiere el módulo CRM activado")

        deals = (
            db.table("crm_deals")
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
            notes = f"El Agente de Ventas detectó que el deal '{deal['name']}' está abierto. Sugerir seguimiento con {contact.get('first_name') or ''} {contact.get('last_name') or ''} ({company.get('name') or 'sin empresa'})."
            try:
                db.table("crm_activities").insert({
                    "org_id": org_id,
                    "deal_id": deal["id"],
                    "contact_id": deal.get("contact_id"),
                    "company_id": deal.get("company_id"),
                    "activity_type": "task",
                    "subject": subject,
                    "notes": notes,
                    "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                    "status": "pending",
                }).execute()
                created += 1
                actions.append({"deal_id": deal["id"], "deal_name": deal["name"], "subject": subject})
            except Exception as e:
                actions.append({"deal_id": deal["id"], "error": str(e)})

        return {"staff_key": staff_key, "actions_created": created, "actions": actions}

    if staff_key == "collector_agent":
        if not _module_enabled(org_id, "collections"):
            raise HTTPException(403, "El agente cobrador requiere el módulo Cobranza activado")

        today = date.today().isoformat()
        invoices = (
            db.table("erp_invoices")
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
            notes = f"El Agente Cobrador detectó que la factura {inv['invoice_number']} por ${float(inv['total']):,.2f} está vencida. Contactar a {contact.get('first_name') or ''} {contact.get('last_name') or ''}."
            try:
                # Si CRM está activo, crear actividad de seguimiento
                if _module_enabled(org_id, "crm"):
                    db.table("crm_activities").insert({
                        "org_id": org_id,
                        "contact_id": inv.get("contact_id"),
                        "activity_type": "task",
                        "subject": subject,
                        "notes": notes,
                        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                        "status": "pending",
                    }).execute()
                created += 1
                actions.append({"invoice_id": inv["id"], "invoice_number": inv["invoice_number"], "total": inv["total"], "subject": subject})
            except Exception as e:
                actions.append({"invoice_id": inv["id"], "error": str(e)})

        return {"staff_key": staff_key, "overdue_invoices": len(overdue), "actions_created": created, "actions": actions}

    if staff_key == "chief_of_staff":
        try:
            runs = tick_chief_of_staff(org_id)
            return {
                "staff_key": staff_key,
                "runs_started": runs,
                "message": "Orquestación diaria ejecutada." if runs else "No se iniciaron runs (staff no habilitado o sin plantillas).",
            }
        except Exception as e:
            raise HTTPException(500, f"Error ejecutando Chief of Staff: {e}")

    raise HTTPException(400, f"Staff key '{staff_key}' no soportado")


@router.get("/chief_of_staff/metrics")
def get_chief_of_staff_metrics(org_id: str = Depends(get_current_org)):
    """Métricas ejecutivas del Chief of Staff: deals, cobranza, inventario y contabilidad."""
    if not _staff_enabled(org_id, "chief_of_staff"):
        raise HTTPException(403, "El AI Staff 'chief_of_staff' no está activado")
    return {
        "staff_key": "chief_of_staff",
        "metrics": get_business_metrics(org_id),
    }
