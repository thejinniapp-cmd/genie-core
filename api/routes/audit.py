"""api/routes/audit.py — Log de auditoría por stream y org"""
from fastapi import APIRouter, Depends
from typing import Optional
import os
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()

def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


@router.get("/")
def list_audit(
    stream_id: Optional[str] = None,
    limit: int = 40,
    org_id: str = Depends(get_current_org),
):
    q = _db().table("audit_log").select("*").eq("org_id", org_id).order("created_at", desc=True).limit(limit)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    data = q.execute().data or []
    return list(reversed(data))  # cronológico
