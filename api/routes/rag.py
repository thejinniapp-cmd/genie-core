"""api/routes/rag.py — fuentes RAG por stream u org"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
from supabase import create_client

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class RAGCreate(BaseModel):
    name: str
    content: str
    source_type: str = "manual"
    stream_id: Optional[str] = None
    always_include: bool = False
    scope: str = "stream"


class PropagateBody(BaseModel):
    target_stream_ids: List[str]


@router.get("/")
def list_rag(stream_id: Optional[str] = None, org_id: str = Header(..., alias="X-Org-Id")):
    q = _db().table("rag_sources").select("*").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    return q.order("created_at").execute().data or []


@router.post("/")
def add_rag(body: RAGCreate, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("rag_sources").insert({
        "org_id": org_id,
        "name": body.name,
        "content": body.content,
        "source_type": body.source_type,
        "stream_id": body.stream_id,
        "always_include": body.always_include,
        "scope": body.scope,
    }).execute()
    return res.data[0] if res.data else {}


@router.post("/{source_id}/propagate")
def propagate_rag(source_id: str, body: PropagateBody, org_id: str = Header(..., alias="X-Org-Id")):
    src = _db().table("rag_sources").select("*").eq("id", source_id).eq("org_id", org_id).single().execute()
    if not src.data:
        raise HTTPException(404, "RAG source not found")
    created = []
    for stream_id in body.target_stream_ids:
        res = _db().table("rag_sources").insert({
            **{k: v for k, v in src.data.items() if k not in ("id", "created_at", "updated_at")},
            "stream_id": stream_id,
            "scope": "stream",
        }).execute()
        if res.data:
            created.append(res.data[0])
    return {"propagated": len(created), "sources": created}


@router.delete("/{source_id}")
def delete_rag(source_id: str, org_id: str = Header(..., alias="X-Org-Id")):
    _db().table("rag_sources").delete().eq("id", source_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}
