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
    db = _db()
    role = body.get("role", "user")

    # Guardar el mensaje
    res = db.table("messages").insert({
        "org_id": org_id,
        "stream_id": stream_id,
        "role": role,
        "content": body.get("content", {}),
        "metadata": body.get("metadata", {}),
    }).execute()
    message = res.data[0] if res.data else {}

    # Si es mensaje del usuario, disparar el agente del stream
    if role == "user":
        content = body.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)

        # Buscar agente activo del stream
        agent_res = (
            db.table("agents")
            .select("*")
            .eq("org_id", org_id)
            .eq("stream_id", stream_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        agent = agent_res.data[0] if agent_res.data else None

        if agent:
            agent_config = {
                "system_prompt": agent.get("system_prompt", "Eres Genie, un asistente de operaciones inteligente."),
                "model_id": agent.get("model_id") or os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-sonnet-4-5"),
                "temperature": agent.get("temperature", 0.3),
                "max_tokens": agent.get("max_tokens", 2048),
                "autonomy_level": agent.get("autonomy_level", "supervised"),
                "tools": agent.get("tools", []),
            }
        else:
            # Sin agente configurado: Genie Core responde con defaults
            agent_config = {
                "system_prompt": (
                    "Eres Genie, el asistente de operaciones de esta organización. "
                    "Tienes memoria del contexto del stream y puedes ayudar a orquestar tareas, "
                    "responder preguntas y ejecutar acciones con las herramientas conectadas."
                ),
                "model_id": os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-sonnet-4-5"),
                "temperature": 0.3,
                "max_tokens": 2048,
                "autonomy_level": "supervised",
                "tools": [],
            }

        db.table("jobs").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "agent_id": agent["id"] if agent else None,
            "agent_type": "prompt",
            "status": "pending",
            "input_data": {
                "message": text,
                "messages": [{"role": "user", "content": text}],
                "stream_id": stream_id,
            },
            "agent_config": agent_config,
            "attempt": 1,
            "priority": 0,
        }).execute()

    return message
