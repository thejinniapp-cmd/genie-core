"""api/routes/connectors.py — gestión de conectores por org"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class ConnectorCreate(BaseModel):
    connector_type: str
    credentials: dict = {}
    config: dict = {}


@router.get("/")
def list_connectors(org_id: str = Header(..., alias="X-Org-Id")):
    return _db().table("connectors").select("*").eq("org_id", org_id).execute().data or []


@router.post("/")
def connect(body: ConnectorCreate, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("connectors").upsert({
        "org_id": org_id,
        "connector_type": body.connector_type,
        "credentials": body.credentials,
        "config": body.config,
        "status": "connected",
    }, on_conflict="org_id,connector_type").execute()
    return res.data[0] if res.data else {}


@router.post("/{connector_type}/test")
def test_connector(connector_type: str, org_id: str = Header(..., alias="X-Org-Id")):
    res = _db().table("connectors").select("*").eq("org_id", org_id).eq("connector_type", connector_type).single().execute()
    if not res.data:
        raise HTTPException(404, "Connector not found")
    return {"status": "ok", "connector_type": connector_type}


@router.delete("/{connector_type}")
def disconnect(connector_type: str, org_id: str = Header(..., alias="X-Org-Id")):
    _db().table("connectors").delete().eq("org_id", org_id).eq("connector_type", connector_type).execute()
    return {"status": "disconnected"}
