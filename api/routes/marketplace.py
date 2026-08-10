"""api/routes/marketplace.py — marketplace de apps, staff y conectores."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client
from datetime import datetime, timezone

from api.auth import get_current_org
from core.workflows.module_defaults import seed_module_workflows

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class ActivateRequest(BaseModel):
    item_slug: str
    source: Optional[str] = "plan"


class DeactivateRequest(BaseModel):
    item_slug: str


def _get_subscription(org_id: str):
    res = (
        _db()
        .table("organization_subscriptions")
        .select("*, plans(*)")
        .eq("organization_id", org_id)
        .limit(1)
        .execute()
    )
    if res and res.data:
        return res.data[0]

    org = _db().table("organizations").select("plan").eq("id", org_id).maybe_single().execute()
    org_data = org.data if org else {}
    plan_slug = org_data.get("plan") if org_data else "starter"
    plan = _db().table("plans").select("*").eq("slug", plan_slug).maybe_single().execute()
    plan_data = plan.data if plan else None
    if not plan_data:
        plan = _db().table("plans").select("*").eq("slug", "starter").maybe_single().execute()
        plan_data = plan.data if plan else None
    if not plan_data:
        raise HTTPException(500, "No se encontró plan por defecto")
    return {"plan_id": plan_data["id"], "plan": plan_data, "status": "active"}


def _is_included(item: dict, plan: dict) -> bool:
    slug = item["slug"]
    item_type = item["item_type"]
    if item_type == "app" and slug in (plan.get("included_modules") or []):
        return True
    if item_type == "ai_staff" and slug in (plan.get("included_ai_staff") or []):
        return True
    if item_type == "connector" and slug.replace("_connector", "") in (plan.get("included_connectors") or []):
        return True
    return False


@router.get("/")
def list_marketplace(org_id: str = Depends(get_current_org)):
    """Lista todo el catálogo con estado para el tenant."""
    sub = _get_subscription(org_id)
    plan = sub.get("plan") or {}

    items = _db().table("marketplace_items").select("*").eq("is_public", True).execute().data or []
    enabled_modules = {
        r["module_key"]: r
        for r in (_db().table("organization_modules").select("*").eq("organization_id", org_id).execute().data or [])
    }
    enabled_staff = {
        r["staff_key"]
        for r in (_db().table("organization_ai_staff").select("staff_key").eq("organization_id", org_id).eq("enabled", True).execute().data or [])
    }
    enabled_connectors = {
        r["connector_type"]
        for r in (_db().table("connectors").select("connector_type").eq("org_id", org_id).eq("status", "connected").execute().data or [])
    }

    result = []
    for item in items:
        slug = item["slug"]
        item_type = item["item_type"]
        included = _is_included(item, plan)
        if item_type == "app":
            enabled = enabled_modules.get(slug, {}).get("enabled", False)
        elif item_type == "ai_staff":
            enabled = slug in enabled_staff
        elif item_type == "connector":
            key = slug.replace("_connector", "")
            enabled = key in enabled_connectors
        else:
            enabled = False

        result.append({
            **item,
            "included_in_plan": included,
            "enabled": enabled,
            "can_activate": True,  # temporal: sin billing aún, cualquier item se activa con un click
        })
    return {"plan": plan, "items": result}


@router.get("/organization")
def get_organization_marketplace_state(org_id: str = Depends(get_current_org)):
    """Estado actual de módulos, staff y conectores del tenant."""
    sub = _get_subscription(org_id)
    modules = _db().table("organization_modules").select("*").eq("organization_id", org_id).execute().data or []
    staff = _db().table("organization_ai_staff").select("*").eq("organization_id", org_id).execute().data or []
    connectors = _db().table("connectors").select("*").eq("org_id", org_id).execute().data or []
    return {
        "plan": sub.get("plan") or {},
        "modules": modules,
        "ai_staff": staff,
        "connectors": connectors,
    }


@router.post("/activate")
def activate_item(body: ActivateRequest, org_id: str = Depends(get_current_org)):
    item = _db().table("marketplace_items").select("*").eq("slug", body.item_slug).single().execute().data
    if not item:
        raise HTTPException(404, "Item no encontrado")

    item_type = item["item_type"]
    now = datetime.now(timezone.utc).isoformat()

    if item_type == "app":
        res = _db().table("organization_modules").upsert({
            "organization_id": org_id,
            "module_key": item["slug"],
            "source": body.source,
            "enabled": True,
            "enabled_at": now,
        }, on_conflict="organization_id,module_key").execute()
        # Seed de plantillas de workflow por defecto del módulo
        workflows = seed_module_workflows(org_id, item["slug"])
        return {"status": "activated", "item": item, "record": res.data[0] if res.data else None, "workflows": workflows}

    if item_type == "ai_staff":
        res = _db().table("organization_ai_staff").upsert({
            "organization_id": org_id,
            "staff_key": item["slug"],
            "source": body.source,
            "enabled": True,
            "enabled_at": now,
        }, on_conflict="organization_id,staff_key").execute()
        # Seed de plantillas de workflow asociadas al AI Staff
        workflows = seed_module_workflows(org_id, item["slug"])
        return {"status": "activated", "item": item, "record": res.data[0] if res.data else None, "workflows": workflows}

    if item_type == "connector":
        connector_type = item["slug"].replace("_connector", "")
        existing = _db().table("connectors").select("*").eq("org_id", org_id).eq("connector_type", connector_type).limit(1).execute()
        existing_data = existing.data if existing else []
        if not existing_data:
            res = _db().table("connectors").insert({
                "org_id": org_id,
                "connector_type": connector_type,
                "status": "pending_setup",
                "config": {"marketplace_item": item["slug"]},
            }).execute()
        return {"status": "connector_ready", "item": item, "connector_type": connector_type}

    raise HTTPException(400, "Tipo de item no soportado")


@router.post("/deactivate")
def deactivate_item(body: DeactivateRequest, org_id: str = Depends(get_current_org)):
    item = _db().table("marketplace_items").select("*").eq("slug", body.item_slug).single().execute().data
    if not item:
        raise HTTPException(404, "Item no encontrado")

    item_type = item["item_type"]

    if item_type == "app":
        _db().table("organization_modules").update({"enabled": False, "disabled_at": datetime.now(timezone.utc).isoformat()}).eq("organization_id", org_id).eq("module_key", item["slug"]).execute()
    elif item_type == "ai_staff":
        _db().table("organization_ai_staff").update({"enabled": False}).eq("organization_id", org_id).eq("staff_key", item["slug"]).execute()
    elif item_type == "connector":
        connector_type = item["slug"].replace("_connector", "")
        _db().table("connectors").delete().eq("org_id", org_id).eq("connector_type", connector_type).execute()

    return {"status": "deactivated", "item": item}
