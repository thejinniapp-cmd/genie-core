"""api/routes/jobs.py — aprobación de jobs por stream"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class RejectBody(BaseModel):
    reason: Optional[str] = None


@router.get("/")
def list_jobs(stream_id: Optional[str] = None, status: Optional[str] = None,
              org_id: str = Header(..., alias="X-Org-Id")):
    q = _db().table("jobs").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    if status:
        q = q.eq("status", status)
    return q.order("created_at", desc=True).execute().data or []


@router.post("/{job_id}/approve")
def approve_job(job_id: str, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("jobs").update({"status": "approved"}).eq("id", job_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Job not found")
    return res.data[0]


@router.post("/{job_id}/reject")
def reject_job(job_id: str, body: RejectBody, org_id: str = Header(..., alias="X-Org-Id")):
    update = {"status": "rejected"}
    if body.reason:
        update["metadata"] = {"rejection_reason": body.reason}
    res = _db().table("jobs").update(update).eq("id", job_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Job not found")
    return res.data[0]
