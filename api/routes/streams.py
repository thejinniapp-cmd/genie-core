"""api/routes/streams.py — CRUD de streams"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

router = APIRouter()

def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class StreamCreate(BaseModel):
    name: str
    description: Optional[str] = None
    type: str = "general"
    config: dict = {}


class StreamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    config: Optional[dict] = None


@router.get("/")
def list_streams(org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("streams").select("*").eq("org_id", org_id).order("created_at").execute()
    return res.data or []


@router.post("/")
def create_stream(body: StreamCreate, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("streams").insert({
        "org_id": org_id,
        "name": body.name,
        "description": body.description,
        "type": body.type,
        "config": body.config,
    }).execute()
    return res.data[0] if res.data else {}


@router.get("/{stream_id}")
def get_stream(stream_id: str, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("streams").select("*").eq("id", stream_id).eq("org_id", org_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Stream not found")
    return res.data


@router.patch("/{stream_id}")
def update_stream(stream_id: str, body: StreamUpdate, org_id: str = Header(..., alias="X-Org-Id")):
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    res = _db().table("streams").update(update).eq("id", stream_id).eq("org_id", org_id).execute()
    return res.data[0] if res.data else {}


@router.get("/{stream_id}/messages")
def get_messages(stream_id: str, limit: int = 50, org_id: str = Header(..., alias="X-Org-Id")):
    res = (
        _db().table("messages")
        .select("*")
        .eq("stream_id", stream_id)
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(res.data or []))


@router.post("/{stream_id}/messages")
def post_message(stream_id: str, body: dict, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("messages").insert({
        "org_id": org_id,
        "stream_id": stream_id,
        "role": body.get("role", "user"),
        "content": body.get("content", {}),
        "metadata": body.get("metadata", {}),
    }).execute()
    return res.data[0] if res.data else {}
