"""api/routes/agents.py — CRUD de agentes por stream"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class AgentCreate(BaseModel):
    name: str
    stream_id: Optional[str] = None
    type: str = "general"
    config: dict = {}
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


@router.get("/")
def list_agents(stream_id: Optional[str] = None, org_id: str = Header(..., alias="X-Org-Id")):
    q = _db().table("agents").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    return q.order("created_at").execute().data or []


@router.post("/")
def create_agent(body: AgentCreate, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("agents").insert({
        "org_id": org_id,
        "name": body.name,
        "stream_id": body.stream_id,
        "type": body.type,
        "config": body.config,
        "enabled": body.enabled,
    }).execute()
    return res.data[0] if res.data else {}


@router.patch("/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate, org_id: str = Header(..., alias="X-Org-Id")):
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    res = _db().table("agents").update(update).eq("id", agent_id).eq("org_id", org_id).execute()
    return res.data[0] if res.data else {}


@router.post("/{agent_id}/test")
def test_agent(agent_id: str, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("agents").select("*").eq("id", agent_id).eq("org_id", org_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Agent not found")
    return {"status": "ok", "agent_id": agent_id, "message": "Agent is reachable"}
