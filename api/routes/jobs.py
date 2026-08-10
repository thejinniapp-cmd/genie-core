"""api/routes/jobs.py — aprobación de jobs por stream"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class RejectBody(BaseModel):
    reason: Optional[str] = None


@router.get("/")
def list_jobs(stream_id: Optional[str] = None, status: Optional[str] = None,
              limit: int = 100, offset: int = 0,
              org_id: str = Depends(get_current_org)):
    q = _db().table("jobs").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    if status:
        q = q.eq("status", status)
    return q.order("created_at", desc=True).limit(min(limit, 500)).offset(offset).execute().data or []


@router.post("/{job_id}/approve")
def approve_job(job_id: str, org_id: str = Depends(get_current_org)):
    res = _db().table("jobs").update({
        "status": "completed",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Job not found")
    return res.data[0]


@router.post("/{job_id}/reject")
def reject_job(job_id: str, body: RejectBody, org_id: str = Depends(get_current_org)):
    update = {"status": "rejected"}
    if body.reason:
        update["metadata"] = {"rejection_reason": body.reason}
    res = _db().table("jobs").update(update).eq("id", job_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Job not found")
    return res.data[0]
