"""api/routes/crm.py — CRM base: empresas, contactos, deals y actividades."""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
import os
import logging
from datetime import datetime, timezone
from supabase import create_client

from api.auth import get_current_org
from core.workflows.scheduler import start_event_run, schedule_module_tick

router = APIRouter()
log = logging.getLogger("genie.crm")


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_crm_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "crm")
        .maybe_single()
        .execute()
        .data
    )
    if not row or not row.get("enabled"):
        raise HTTPException(403, "El módulo CRM no está activado para esta organización")


# ── Compañías ───────────────────────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    name: str
    legal_name: Optional[str] = None
    tax_id: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = "MX"
    status: Optional[str] = "active"
    source: Optional[str] = "manual"
    metadata: Optional[dict] = {}


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    legal_name: Optional[str] = None
    tax_id: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/companies")
def list_companies(org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    rows = _db().table("crm_companies").select("*").eq("org_id", org_id).order("name").execute().data or []
    return rows


@router.post("/companies")
def create_company(body: CompanyCreate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("crm_companies").insert(data).execute()
    return res.data[0]


@router.get("/companies/{company_id}")
def get_company(company_id: str, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    row = _db().table("crm_companies").select("*").eq("id", company_id).eq("org_id", org_id).single().execute().data
    if not row:
        raise HTTPException(404, "Empresa no encontrada")
    return row


@router.patch("/companies/{company_id}")
def update_company(company_id: str, body: CompanyUpdate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("crm_companies").update(data).eq("id", company_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Empresa no encontrada")
    return res.data[0]


@router.delete("/companies/{company_id}")
def delete_company(company_id: str, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    _db().table("crm_companies").delete().eq("id", company_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}


# ── Contactos ───────────────────────────────────────────────────────────────────

class ContactCreate(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    company_id: Optional[str] = None
    is_primary: Optional[bool] = False
    status: Optional[str] = "active"
    source: Optional[str] = "manual"
    metadata: Optional[dict] = {}


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    company_id: Optional[str] = None
    is_primary: Optional[bool] = None
    status: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/contacts")
def list_contacts(company_id: Optional[str] = None, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    q = _db().table("crm_contacts").select("*, crm_companies(name)").eq("org_id", org_id).order("first_name")
    if company_id:
        q = q.eq("company_id", company_id)
    rows = q.execute().data or []
    return rows


@router.post("/contacts")
def create_contact(body: ContactCreate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("crm_contacts").insert(data).execute()
    contact = res.data[0]

    try:
        start_event_run(
            org_id=org_id,
            module_key="crm",
            event="new_contact",
            input_data={"contact_id": contact["id"]},
            name=f"Seguimiento: {contact.get('first_name') or ''} {contact.get('last_name') or ''}".strip(),
            metadata={"contact_id": contact["id"]},
        )
    except Exception as e:
        log.warning(f"[crm] Could not trigger new_contact workflow: {e}")

    return contact


@router.get("/contacts/{contact_id}")
def get_contact(contact_id: str, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    row = _db().table("crm_contacts").select("*, crm_companies(name)").eq("id", contact_id).eq("org_id", org_id).single().execute().data
    if not row:
        raise HTTPException(404, "Contacto no encontrado")
    return row


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: str, body: ContactUpdate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("crm_contacts").update(data).eq("id", contact_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Contacto no encontrado")
    return res.data[0]


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: str, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    _db().table("crm_contacts").delete().eq("id", contact_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}


# ── Pipelines y etapas ──────────────────────────────────────────────────────────

class PipelineCreate(BaseModel):
    name: str
    is_default: Optional[bool] = False


class StageCreate(BaseModel):
    pipeline_id: str
    name: str
    order_index: Optional[int] = 0
    win_probability: Optional[float] = 0


@router.get("/pipelines")
def list_pipelines(org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    rows = _db().table("crm_pipelines").select("*, crm_stages(*)").eq("org_id", org_id).order("created_at").execute().data or []
    # Normalizar nested stages a nombre 'stages' para el frontend
    for row in rows:
        if "crm_stages" in row:
            row["stages"] = row.pop("crm_stages")
    return rows


@router.post("/pipelines")
def create_pipeline(body: PipelineCreate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = {"name": body.name, "is_default": body.is_default, "org_id": org_id}
    res = _db().table("crm_pipelines").insert(data).execute()
    pipeline = res.data[0]
    # Crear etapas por defecto
    default_stages = [
        {"pipeline_id": pipeline["id"], "org_id": org_id, "name": "Nuevo", "order_index": 0, "win_probability": 10},
        {"pipeline_id": pipeline["id"], "org_id": org_id, "name": "Calificación", "order_index": 1, "win_probability": 25},
        {"pipeline_id": pipeline["id"], "org_id": org_id, "name": "Propuesta", "order_index": 2, "win_probability": 50},
        {"pipeline_id": pipeline["id"], "org_id": org_id, "name": "Negociación", "order_index": 3, "win_probability": 75},
        {"pipeline_id": pipeline["id"], "org_id": org_id, "name": "Cerrado ganado", "order_index": 4, "win_probability": 100},
        {"pipeline_id": pipeline["id"], "org_id": org_id, "name": "Cerrado perdido", "order_index": 5, "win_probability": 0},
    ]
    stages_res = _db().table("crm_stages").insert(default_stages).execute()
    pipeline["stages"] = stages_res.data or []
    return pipeline


@router.post("/stages")
def create_stage(body: StageCreate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("crm_stages").insert(data).execute()
    return res.data[0]


# ── Deals / Oportunidades ───────────────────────────────────────────────────────

class DealCreate(BaseModel):
    name: str
    pipeline_id: Optional[str] = None
    stage_id: Optional[str] = None
    company_id: Optional[str] = None
    contact_id: Optional[str] = None
    value: Optional[float] = 0
    currency: Optional[str] = "MXN"
    expected_close_date: Optional[str] = None
    status: Optional[str] = "open"
    source: Optional[str] = "manual"
    metadata: Optional[dict] = {}


class DealUpdate(BaseModel):
    name: Optional[str] = None
    pipeline_id: Optional[str] = None
    stage_id: Optional[str] = None
    company_id: Optional[str] = None
    contact_id: Optional[str] = None
    value: Optional[float] = None
    currency: Optional[str] = None
    expected_close_date: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/deals")
def list_deals(pipeline_id: Optional[str] = None, stage_id: Optional[str] = None, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    q = _db().table("crm_deals").select("*, crm_companies(name), crm_contacts(first_name, last_name), crm_stages(name)").eq("org_id", org_id)
    if pipeline_id:
        q = q.eq("pipeline_id", pipeline_id)
    if stage_id:
        q = q.eq("stage_id", stage_id)
    rows = q.order("created_at", desc=True).execute().data or []
    return rows


@router.post("/deals")
def create_deal(body: DealCreate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("crm_deals").insert(data).execute()
    deal = res.data[0]

    try:
        start_event_run(
            org_id=org_id,
            module_key="crm",
            event="new_deal",
            input_data={"deal_id": deal["id"]},
            name=f"Seguimiento: {deal.get('name', 'Deal')}",
            metadata={"deal_id": deal["id"]},
        )
    except Exception as e:
        log.warning(f"[crm] Could not trigger new_deal workflow: {e}")

    schedule_module_tick(org_id, "crm", background_tasks)
    return deal


@router.get("/deals/{deal_id}")
def get_deal(deal_id: str, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    row = _db().table("crm_deals").select("*, crm_companies(name), crm_contacts(first_name, last_name), crm_stages(name)").eq("id", deal_id).eq("org_id", org_id).single().execute().data
    if not row:
        raise HTTPException(404, "Oportunidad no encontrada")
    return row


@router.patch("/deals/{deal_id}")
def update_deal(deal_id: str, body: DealUpdate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("crm_deals").update(data).eq("id", deal_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Oportunidad no encontrada")
    schedule_module_tick(org_id, "crm", background_tasks)
    return res.data[0]


@router.delete("/deals/{deal_id}")
def delete_deal(deal_id: str, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    _db().table("crm_deals").delete().eq("id", deal_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}


# ── Actividades ─────────────────────────────────────────────────────────────────

class ActivityCreate(BaseModel):
    deal_id: Optional[str] = None
    company_id: Optional[str] = None
    contact_id: Optional[str] = None
    activity_type: str = "note"  # call | email | meeting | note | task | whatsapp
    subject: Optional[str] = None
    notes: Optional[str] = None
    scheduled_at: Optional[str] = None
    status: Optional[str] = "pending"


class ActivityUpdate(BaseModel):
    activity_type: Optional[str] = None
    subject: Optional[str] = None
    notes: Optional[str] = None
    scheduled_at: Optional[str] = None
    status: Optional[str] = None


@router.get("/activities")
def list_activities(deal_id: Optional[str] = None, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    q = _db().table("crm_activities").select("*").eq("org_id", org_id).order("created_at", desc=True)
    if deal_id:
        q = q.eq("deal_id", deal_id)
    rows = q.execute().data or []
    return rows


@router.post("/activities")
def create_activity(body: ActivityCreate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("crm_activities").insert(data).execute()
    return res.data[0]


@router.patch("/activities/{activity_id}")
def update_activity(activity_id: str, body: ActivityUpdate, org_id: str = Depends(get_current_org)):
    _ensure_crm_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    if data.get("status") == "completed":
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("crm_activities").update(data).eq("id", activity_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Actividad no encontrada")
    return res.data[0]
