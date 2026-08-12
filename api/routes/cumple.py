"""api/routes/cumple.py — Genie Cumple: calendario fiscal, CFDI, simulador de régimen"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
from datetime import datetime, timezone, date, timedelta
from supabase import create_client

from api.auth import get_current_org
from core.workflows.actions import (
    import_cfdi,
    audit_cfdi,
    generate_fiscal_calendar,
    simulate_tax_regime,
    get_inventory_fiscal_impact,
)

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_cumple_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "cumple")
        .maybe_single()
        .execute()
        .data
    )
    if not row or not row.get("enabled"):
        raise HTTPException(403, "El módulo Genie Cumple no está activado")


# ── Perfil fiscal ─────────────────────────────────────────────────────────────

class FiscalProfileUpdate(BaseModel):
    rfc: Optional[str] = None
    razon_social: Optional[str] = None
    regimen_fiscal: Optional[str] = None
    regimen_since: Optional[str] = None
    isr_rate_pct: Optional[float] = None


@router.get("/fiscal-profile")
def get_fiscal_profile(org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    rows = _db().table("fiscal_profile").select("*").eq("org_id", org_id).limit(1).execute().data or []
    return rows[0] if rows else {"org_id": org_id, "rfc": None, "razon_social": None, "regimen_fiscal": None}


@router.put("/fiscal-profile")
def upsert_fiscal_profile(body: FiscalProfileUpdate, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    existing = _db().table("fiscal_profile").select("metadata").eq("org_id", org_id).limit(1).execute().data or []
    metadata = existing[0].get("metadata") or {} if existing else {}
    if body.isr_rate_pct is not None:
        metadata["isr_rate_pct"] = body.isr_rate_pct

    data = body.model_dump(exclude_unset=True, exclude={"isr_rate_pct"})
    data["org_id"] = org_id
    data["metadata"] = metadata
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("fiscal_profile").upsert(data, on_conflict="org_id").execute()
    return res.data[0]


# ── Obligaciones fiscales ─────────────────────────────────────────────────────

class ObligationCreate(BaseModel):
    name: str
    obligation_type: str  # isr_mensual | iva_mensual | declaracion_anual | otro
    frequency: str = "monthly"  # monthly | annual
    due_day: int = 17
    due_month: Optional[int] = None
    months_offset: int = 1
    notes: Optional[str] = None


class ObligationUpdate(BaseModel):
    name: Optional[str] = None
    due_day: Optional[int] = None
    due_month: Optional[int] = None
    months_offset: Optional[int] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


DEFAULT_OBLIGATIONS = [
    {"name": "Pago provisional ISR mensual", "obligation_type": "isr_mensual", "frequency": "monthly", "due_day": 17, "months_offset": 1},
    {"name": "Pago definitivo IVA mensual", "obligation_type": "iva_mensual", "frequency": "monthly", "due_day": 17, "months_offset": 1},
    {"name": "Declaración anual (confirma la fecha exacta con tu contador)", "obligation_type": "declaracion_anual", "frequency": "annual", "due_day": 31, "due_month": 3},
]


@router.get("/obligations")
def list_obligations(org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    return _db().table("fiscal_obligations").select("*").eq("org_id", org_id).eq("is_active", True).order("name").execute().data or []


@router.post("/obligations")
def create_obligation(body: ObligationCreate, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("fiscal_obligations").insert(data).execute()
    return res.data[0]


@router.post("/obligations/seed-defaults")
def seed_default_obligations(org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    existing = _db().table("fiscal_obligations").select("name").eq("org_id", org_id).execute().data or []
    existing_names = {r["name"] for r in existing}
    to_insert = [{**o, "org_id": org_id} for o in DEFAULT_OBLIGATIONS if o["name"] not in existing_names]
    if not to_insert:
        return {"seeded": 0}
    res = _db().table("fiscal_obligations").insert(to_insert).execute()
    return {"seeded": len(res.data)}


@router.patch("/obligations/{obligation_id}")
def update_obligation(obligation_id: str, body: ObligationUpdate, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("fiscal_obligations").update(data).eq("id", obligation_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Obligación no encontrada")
    return res.data[0]


# ── Calendario ─────────────────────────────────────────────────────────────────

@router.get("/calendar")
def get_calendar(months_ahead: int = 3, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    result = generate_fiscal_calendar(org_id, months_ahead=months_ahead)
    soon_cutoff = (date.today() + timedelta(days=7)).isoformat()
    for inst in result.get("instances") or []:
        inst["vence_pronto"] = inst.get("status") == "pending" and inst.get("due_date") and inst["due_date"] <= soon_cutoff
    return result


class CalendarInstanceUpdate(BaseModel):
    status: str  # pending | prepared | filed


@router.patch("/calendar/{instance_id}")
def update_calendar_instance(instance_id: str, body: CalendarInstanceUpdate, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    res = (
        _db()
        .table("fiscal_obligation_instances")
        .update({"status": body.status, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", instance_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Instancia de calendario no encontrada")
    return res.data[0]


# ── CFDI ──────────────────────────────────────────────────────────────────────

class CfdiImport(BaseModel):
    xml: str


class CfdiUpdate(BaseModel):
    is_efos_flagged: Optional[bool] = None
    status: Optional[str] = None  # vigente | cancelado


@router.post("/cfdi/import")
def import_cfdi_route(body: CfdiImport, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    result = import_cfdi(org_id, body.xml)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "No se pudo importar el CFDI"))
    return result["cfdi"]


@router.get("/cfdi")
def list_cfdi(status: Optional[str] = None, rfc_emisor: Optional[str] = None, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    q = _db().table("cfdi_documents").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    if rfc_emisor:
        q = q.eq("rfc_emisor", rfc_emisor)
    return q.order("fecha_emision", desc=True).execute().data or []


@router.patch("/cfdi/{cfdi_id}")
def update_cfdi(cfdi_id: str, body: CfdiUpdate, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    res = _db().table("cfdi_documents").update(data).eq("id", cfdi_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "CFDI no encontrado")
    return res.data[0]


@router.get("/cfdi/audit")
def get_cfdi_audit(months: int = 3, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    return audit_cfdi(org_id, months=months)


# ── Simulador de régimen e inventario ──────────────────────────────────────────

class RegimeItem(BaseModel):
    name: str
    estimated_rate_pct: float


class RegimeSimulationRequest(BaseModel):
    regimes: List[RegimeItem]


@router.post("/regime-simulation")
def post_regime_simulation(body: RegimeSimulationRequest, org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    return simulate_tax_regime(org_id, [r.model_dump() for r in body.regimes])


@router.get("/inventory-fiscal-impact")
def get_inventory_impact(org_id: str = Depends(get_current_org)):
    _ensure_cumple_enabled(org_id)
    return get_inventory_fiscal_impact(org_id)
