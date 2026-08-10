"""api/routes/accounting.py — Contabilidad Light: ingresos, gastos y resumen."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
from datetime import datetime, timezone, date
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_accounting_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "accounting")
        .maybe_single()
        .execute()
        .data
    )
    if not row or not row.get("enabled"):
        raise HTTPException(403, "El módulo Contabilidad Light no está activado")


DEFAULT_ACCOUNTS = [
    ("Ventas", "income", "#22C55E"),
    ("Servicios", "income", "#16A34A"),
    ("Rentas", "expense", "#EF4444"),
    ("Salarios", "expense", "#F97316"),
    ("Proveedores", "expense", "#EAB308"),
    ("Software y herramientas", "expense", "#7F77DD"),
    ("Impuestos", "expense", "#3B82F6"),
    ("Otros gastos", "expense", "#6B7280"),
]


# ── Cuentas ─────────────────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    name: str
    type: str  # income | expense | asset | liability
    color: Optional[str] = "#7F77DD"
    description: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/accounts")
def list_accounts(org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    rows = _db().table("erp_accounts").select("*").eq("org_id", org_id).eq("is_active", True).order("type").order("name").execute().data or []
    return rows


@router.post("/accounts")
def create_account(body: AccountCreate, org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    try:
        res = _db().table("erp_accounts").insert(data).execute()
    except Exception as e:
        raise HTTPException(409, f"Ya existe una cuenta con ese nombre: {e}")
    return res.data[0]


@router.patch("/accounts/{account_id}")
def update_account(account_id: str, body: AccountUpdate, org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("erp_accounts").update(data).eq("id", account_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Cuenta no encontrada")
    return res.data[0]


@router.post("/accounts/seed")
def seed_default_accounts(org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    existing = _db().table("erp_accounts").select("name").eq("org_id", org_id).execute().data or []
    existing_names = {r["name"] for r in existing}
    to_insert = [
        {"org_id": org_id, "name": name, "type": type_, "color": color}
        for name, type_, color in DEFAULT_ACCOUNTS
        if name not in existing_names
    ]
    if not to_insert:
        return {"seeded": 0}
    res = _db().table("erp_accounts").insert(to_insert).execute()
    return {"seeded": len(res.data)}


# ── Transacciones ──────────────────────────────────────────────────────────────

class TransactionCreate(BaseModel):
    account_id: Optional[str] = None
    type: str  # income | expense | transfer
    amount: float
    currency: Optional[str] = "MXN"
    transaction_date: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    contact_id: Optional[str] = None
    invoice_id: Optional[str] = None
    is_recurring: Optional[bool] = False


class TransactionUpdate(BaseModel):
    account_id: Optional[str] = None
    type: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    transaction_date: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    contact_id: Optional[str] = None
    invoice_id: Optional[str] = None
    is_recurring: Optional[bool] = None


@router.get("/transactions")
def list_transactions(
    type: Optional[str] = None,
    account_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    org_id: str = Depends(get_current_org),
):
    _ensure_accounting_enabled(org_id)
    q = (
        _db()
        .table("erp_transactions")
        .select("*, erp_accounts(name, type, color), crm_contacts(first_name,last_name)")
        .eq("org_id", org_id)
    )
    if type:
        q = q.eq("type", type)
    if account_id:
        q = q.eq("account_id", account_id)
    if start:
        q = q.gte("transaction_date", start)
    if end:
        q = q.lte("transaction_date", end)
    rows = q.order("transaction_date", desc=True).order("created_at", desc=True).execute().data or []
    return rows


@router.post("/transactions")
def create_transaction(body: TransactionCreate, org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    data["transaction_date"] = body.transaction_date or date.today().isoformat()
    res = _db().table("erp_transactions").insert(data).execute()
    return res.data[0]


@router.patch("/transactions/{transaction_id}")
def update_transaction(transaction_id: str, body: TransactionUpdate, org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("erp_transactions").update(data).eq("id", transaction_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Transacción no encontrada")
    return res.data[0]


@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: str, org_id: str = Depends(get_current_org)):
    _ensure_accounting_enabled(org_id)
    _db().table("erp_transactions").delete().eq("id", transaction_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}


# ── Resumen contable ───────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(
    start: Optional[str] = None,
    end: Optional[str] = None,
    org_id: str = Depends(get_current_org),
):
    _ensure_accounting_enabled(org_id)
    q = _db().table("erp_transactions").select("type, amount, account_id, erp_accounts(name, type, color)").eq("org_id", org_id)
    if start:
        q = q.gte("transaction_date", start)
    if end:
        q = q.lte("transaction_date", end)
    rows = q.execute().data or []

    income = 0.0
    expense = 0.0
    by_account: dict = {}
    for r in rows:
        amount = float(r.get("amount") or 0)
        t = r.get("type")
        account = r.get("erp_accounts") or {}
        if t == "income":
            income += amount
        elif t == "expense":
            expense += amount

        aid = r.get("account_id") or "none"
        if aid not in by_account:
            by_account[aid] = {
                "id": aid,
                "name": account.get("name") or "Sin categoría",
                "type": account.get("type") or t,
                "color": account.get("color") or "#7F77DD",
                "income": 0.0,
                "expense": 0.0,
            }
        if t == "income":
            by_account[aid]["income"] += amount
        elif t == "expense":
            by_account[aid]["expense"] += amount

    return {
        "income_total": round(income, 2),
        "expense_total": round(expense, 2),
        "net_total": round(income - expense, 2),
        "transaction_count": len(rows),
        "by_account": list(by_account.values()),
    }
