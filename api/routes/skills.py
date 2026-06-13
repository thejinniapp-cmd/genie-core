"""api/routes/skills.py — catálogo e instalación de skills"""
from fastapi import APIRouter, Header
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class SkillInstall(BaseModel):
    skill_id: str
    stream_id: str
    config: dict = {}


@router.get("/")
def list_skills(category: Optional[str] = None):
    q = _db().table("skills").select("*")
    if category:
        q = q.eq("category", category)
    return q.order("name").execute().data or []


@router.post("/install")
def install_skill(body: SkillInstall, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("stream_skills").upsert({
        "org_id": org_id,
        "skill_id": body.skill_id,
        "stream_id": body.stream_id,
        "config": body.config,
        "enabled": True,
    }, on_conflict="org_id,skill_id,stream_id").execute()
    return res.data[0] if res.data else {}
