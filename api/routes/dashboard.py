"""api/routes/dashboard.py — métricas y audit log"""
from fastapi import APIRouter, Depends
from typing import Optional
import os
from datetime import date
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


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
    except Exception:
        return False


@router.get("/metrics")
def get_metrics(org_id: str = Depends(get_current_org)):
    db = _db()
    streams = db.table("streams").select("id", count="exact").eq("org_id", org_id).execute()
    agents = db.table("agents").select("id", count="exact").eq("org_id", org_id).execute()
    jobs_pending = db.table("jobs").select("id", count="exact").eq("org_id", org_id).eq("status", "pending").execute()
    jobs_done = db.table("jobs").select("id", count="exact").eq("org_id", org_id).eq("status", "completed").execute()

    enabled_modules = {
        r["module_key"]
        for r in (db.table("organization_modules").select("module_key").eq("organization_id", org_id).eq("enabled", True).execute().data or [])
    }

    business = {}

    # CRM
    if _module_enabled(org_id, "crm"):
        try:
            contacts = db.table("crm_contacts").select("id", count="exact").eq("org_id", org_id).execute().count or 0
            companies = db.table("crm_companies").select("id", count="exact").eq("org_id", org_id).execute().count or 0
            deals = db.table("crm_deals").select("status, value").eq("org_id", org_id).execute().data or []
            open_deals = [d for d in deals if d.get("status") == "open"]
            business["crm"] = {
                "contacts": contacts,
                "companies": companies,
                "deals": len(deals),
                "open_deals": len(open_deals),
                "open_deals_value": round(sum(float(d.get("value") or 0) for d in open_deals), 2),
            }
        except Exception:
            business["crm"] = {"contacts": 0, "companies": 0, "deals": 0, "open_deals": 0, "open_deals_value": 0}

    # Cobranza
    if _module_enabled(org_id, "collections"):
        try:
            today = date.today().isoformat()
            invoices = db.table("erp_invoices").select("status, total, due_date").eq("org_id", org_id).execute().data or []
            total_invoiced = sum(float(i.get("total") or 0) for i in invoices)
            total_paid = sum(float(i.get("total") or 0) for i in invoices if i.get("status") == "paid")
            total_outstanding = sum(
                float(i.get("total") or 0)
                for i in invoices
                if i.get("status") in ("sent", "partial", "overdue")
            )
            total_overdue = sum(
                float(i.get("total") or 0)
                for i in invoices
                if i.get("status") == "overdue"
                or (i.get("status") in ("sent", "partial") and i.get("due_date") and i.get("due_date") < today)
            )
            business["collections"] = {
                "total_invoiced": round(total_invoiced, 2),
                "total_paid": round(total_paid, 2),
                "total_outstanding": round(total_outstanding, 2),
                "total_overdue": round(total_overdue, 2),
                "invoices_count": len(invoices),
            }
        except Exception:
            business["collections"] = {
                "total_invoiced": 0, "total_paid": 0, "total_outstanding": 0, "total_overdue": 0, "invoices_count": 0
            }

    # Contabilidad
    if _module_enabled(org_id, "accounting"):
        try:
            transactions = db.table("erp_transactions").select("type, amount").eq("org_id", org_id).execute().data or []
            income = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "income")
            expense = sum(float(t.get("amount") or 0) for t in transactions if t.get("type") == "expense")
            business["accounting"] = {
                "income_total": round(income, 2),
                "expense_total": round(expense, 2),
                "net_total": round(income - expense, 2),
                "transaction_count": len(transactions),
            }
        except Exception:
            business["accounting"] = {"income_total": 0, "expense_total": 0, "net_total": 0, "transaction_count": 0}

    # Inventario
    if _module_enabled(org_id, "inventory"):
        try:
            items = db.table("inventory_items").select("current_stock, min_stock, cost_price").eq("org_id", org_id).eq("is_active", True).execute().data or []
            total_items = len(items)
            low_stock = sum(1 for i in items if float(i.get("current_stock") or 0) <= float(i.get("min_stock") or 0))
            total_value = sum(float(i.get("current_stock") or 0) * float(i.get("cost_price") or 0) for i in items)
            pending_pos = db.table("inventory_purchase_orders").select("id", count="exact").eq("org_id", org_id).in_("status", ["draft", "sent", "partial"]).execute().count or 0
            business["inventory"] = {
                "total_items": total_items,
                "low_stock": low_stock,
                "total_value": round(total_value, 2),
                "pending_purchase_orders": pending_pos,
            }
        except Exception:
            business["inventory"] = {"total_items": 0, "low_stock": 0, "total_value": 0, "pending_purchase_orders": 0}

    return {
        "streams": streams.count or 0,
        "agents": agents.count or 0,
        "jobs_pending": jobs_pending.count or 0,
        "jobs_completed": jobs_done.count or 0,
        "enabled_modules": list(enabled_modules),
        "business": business,
    }


@router.get("/audit")
def get_audit(stream_id: Optional[str] = None, limit: int = 100,
              org_id: str = Depends(get_current_org)):
    q = _db().table("audit_log").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    return q.order("created_at", desc=True).limit(limit).execute().data or []
