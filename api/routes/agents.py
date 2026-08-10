"""api/routes/agents.py — CRUD de agentes por stream"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class AgentCreate(BaseModel):
    name: str
    display_name: Optional[str] = None
    stream_id: Optional[str] = None
    type: str = "custom"
    system_prompt: Optional[str] = None
    config: dict = {}
    is_active: bool = True


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    system_prompt: Optional[str] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


@router.get("/")
def list_agents(stream_id: Optional[str] = None, org_id: str = Depends(get_current_org)):
    q = _db().table("agents").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    return q.order("created_at").execute().data or []


@router.post("/")
def create_agent(body: AgentCreate, org_id: str = Depends(get_current_org)):
    res = _db().table("agents").insert({
        "org_id": org_id,
        "name": body.name,
        "display_name": body.display_name or body.name,
        "stream_id": body.stream_id,
        "type": body.type,
        "system_prompt": body.system_prompt,
        "config": body.config,
        "is_active": body.is_active,
    }).execute()
    return res.data[0] if res.data else {}


@router.patch("/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate, org_id: str = Depends(get_current_org)):
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    res = _db().table("agents").update(update).eq("id", agent_id).eq("org_id", org_id).execute()
    return res.data[0] if res.data else {}


@router.post("/{agent_id}/test")
def test_agent(agent_id: str, org_id: str = Depends(get_current_org)):
    res = _db().table("agents").select("*").eq("id", agent_id).eq("org_id", org_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Agent not found")
    return {"status": "ok", "agent_id": agent_id, "message": "Agent is reachable"}
