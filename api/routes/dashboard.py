"""api/routes/dashboard.py — métricas y audit log"""
from fastapi import APIRouter, Header
from typing import Optional
import os
from supabase import create_client

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


@router.get("/metrics")
def get_metrics(org_id: str = Header(..., alias="X-Org-Id")):
    db = _db()
    streams = db.table("streams").select("id", count="exact").eq("org_id", org_id).execute()
    agents = db.table("agents").select("id", count="exact").eq("org_id", org_id).execute()
    jobs_pending = db.table("jobs").select("id", count="exact").eq("org_id", org_id).eq("status", "pending").execute()
    jobs_done = db.table("jobs").select("id", count="exact").eq("org_id", org_id).eq("status", "approved").execute()
    return {
        "streams": streams.count or 0,
        "agents": agents.count or 0,
        "jobs_pending": jobs_pending.count or 0,
        "jobs_approved": jobs_done.count or 0,
    }


@router.get("/audit")
def get_audit(stream_id: Optional[str] = None, limit: int = 100,
              org_id: str = Header(..., alias="X-Org-Id")):
    q = _db().table("audit_log").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    return q.order("created_at", desc=True).limit(limit).execute().data or []
