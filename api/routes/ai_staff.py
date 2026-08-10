"""api/routes/ai_staff.py — AI Staff: agentes de ventas y cobrador."""
import os
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import create_client

from core.workflows.scheduler import tick_chief_of_staff
from core.workflows.actions import get_business_metrics
from core.workflows.chief_alerts import (
    check_and_send_alerts,
    get_alert_settings,
    upsert_alert_settings,
)
from core.workflows.ai_staff_runner import run_sales_agent, run_collector_agent
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

    if staff_key == "sales_agent":
        if not _module_enabled(org_id, "crm"):
            raise HTTPException(403, "El agente de ventas requiere el módulo CRM activado")
        return run_sales_agent(org_id)

    if staff_key == "collector_agent":
        if not _module_enabled(org_id, "collections"):
            raise HTTPException(403, "El agente cobrador requiere el módulo Cobranza activado")
        return run_collector_agent(org_id)

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


class AlertSettingUpdate(BaseModel):
    metric_key: str
    threshold: float
    enabled: bool = True


class AlertSettingsPayload(BaseModel):
    settings: List[AlertSettingUpdate]


@router.get("/chief_of_staff/alerts/settings")
def get_chief_alert_settings(org_id: str = Depends(get_current_org)):
    """Devuelve la configuración de alertas del Chief of Staff."""
    if not _staff_enabled(org_id, "chief_of_staff"):
        raise HTTPException(403, "El AI Staff 'chief_of_staff' no está activado")
    return {"settings": get_alert_settings(org_id)}


@router.post("/chief_of_staff/alerts/settings")
def update_chief_alert_settings(body: AlertSettingsPayload, org_id: str = Depends(get_current_org)):
    """Actualiza umbrales y habilitación de alertas del Chief of Staff."""
    if not _staff_enabled(org_id, "chief_of_staff"):
        raise HTTPException(403, "El AI Staff 'chief_of_staff' no está activado")
    updated = upsert_alert_settings(org_id, [s.model_dump() for s in body.settings])
    return {"updated": updated}


@router.post("/chief_of_staff/alerts/check")
def check_chief_alerts_now(org_id: str = Depends(get_current_org)):
    """Ejecuta manualmente la revisión de alertas del Chief of Staff."""
    if not _staff_enabled(org_id, "chief_of_staff"):
        raise HTTPException(403, "El AI Staff 'chief_of_staff' no está activado")
    return check_and_send_alerts(org_id)
