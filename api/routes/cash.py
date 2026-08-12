"""api/routes/cash.py — Genie Caja: forecast de 13 semanas, concentración de clientes, métricas de cobranza"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from datetime import datetime, timezone, date
from supabase import create_client

from api.auth import get_current_org
from core.workflows.actions import (
    compute_cash_forecast,
    compute_customer_concentration,
    compute_days_to_collect,
    suggest_credit_instrument,
)

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_collections_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "collections")
        .maybe_single()
        .execute()
        .data
    )
    if not row or not row.get("enabled"):
        raise HTTPException(403, "El módulo Cobranza + Facturación no está activado")


# ── Compromisos recurrentes ──────────────────────────────────────────────────

class RecurringCommitmentCreate(BaseModel):
    name: str
    type: str  # income | expense
    amount: float = 0
    frequency: str = "monthly"  # weekly | biweekly | monthly
    day_of_month: Optional[int] = None
    weekday: Optional[int] = None
    account_id: Optional[str] = None
    notes: Optional[str] = None


class RecurringCommitmentUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    amount: Optional[float] = None
    frequency: Optional[str] = None
    day_of_month: Optional[int] = None
    weekday: Optional[int] = None
    account_id: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


@router.get("/recurring-commitments")
def list_recurring_commitments(org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    return (
        _db()
        .table("erp_recurring_commitments")
        .select("*")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .order("name")
        .execute()
        .data
        or []
    )


@router.post("/recurring-commitments")
def create_recurring_commitment(body: RecurringCommitmentCreate, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("erp_recurring_commitments").insert(data).execute()
    return res.data[0]


@router.patch("/recurring-commitments/{commitment_id}")
def update_recurring_commitment(commitment_id: str, body: RecurringCommitmentUpdate, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("erp_recurring_commitments").update(data).eq("id", commitment_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Compromiso recurrente no encontrado")
    return res.data[0]


# ── Checkpoints de saldo ──────────────────────────────────────────────────────

class CashBalanceCreate(BaseModel):
    balance: float
    as_of_date: Optional[str] = None
    notes: Optional[str] = None


@router.get("/cash-balance")
def list_cash_balance(org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    return (
        _db()
        .table("erp_cash_balance_snapshots")
        .select("*")
        .eq("org_id", org_id)
        .order("as_of_date", desc=True)
        .limit(20)
        .execute()
        .data
        or []
    )


@router.post("/cash-balance")
def create_cash_balance(body: CashBalanceCreate, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    data["as_of_date"] = body.as_of_date or date.today().isoformat()
    res = _db().table("erp_cash_balance_snapshots").insert(data).execute()
    return res.data[0]


# ── Forecast, concentración, crédito, métricas ────────────────────────────────

@router.get("/forecast")
def get_forecast(weeks: int = 13, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    return compute_cash_forecast(org_id, weeks=weeks)


@router.get("/concentration")
def get_concentration(months: int = 12, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    return compute_customer_concentration(org_id, months=months)


@router.get("/credit-recommendation")
def get_credit_recommendation(org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    forecast = compute_cash_forecast(org_id)
    recommendation = suggest_credit_instrument(forecast)
    return {"forecast_summary": {"runway_weeks": forecast["runway_weeks"], "first_negative_week": forecast["first_negative_week"]}, "recommendation": recommendation}


@router.get("/metrics")
def get_cash_metrics(org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    forecast = compute_cash_forecast(org_id)
    return {
        "days_to_collect": compute_days_to_collect(org_id),
        "runway_weeks": forecast["runway_weeks"],
    }
