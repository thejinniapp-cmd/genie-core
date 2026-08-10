"""api/routes/notifications.py — CRUD de notificaciones"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import os
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


@router.get("/")
def list_notifications(
    limit: int = 30,
    unread_only: bool = False,
    org_id: str = Depends(get_current_org),
):
    """Lista notificaciones del org, más recientes primero."""
    q = (
        _db()
        .table("stream_notifications")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(limit)
    )
    if unread_only:
        q = q.eq("status", "pending")
    res = q.execute()
    return res.data or []


@router.get("/unread-count")
def unread_count(org_id: str = Depends(get_current_org)):
    """Conteo rápido de notificaciones no leídas."""
    res = (
        _db()
        .table("stream_notifications")
        .select("id", count="exact")
        .eq("org_id", org_id)
        .eq("status", "pending")
        .execute()
    )
    return {"count": res.count or 0}


@router.post("/{notif_id}/read")
def mark_read(notif_id: str, org_id: str = Depends(get_current_org)):
    """Marca una notificación como leída."""
    _db().table("stream_notifications").update({"status": "dismissed"}).eq("id", notif_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(org_id: str = Depends(get_current_org)):
    """Marca todas las notificaciones pendientes como leídas."""
    from datetime import datetime, timezone
    _db().table("stream_notifications").update({
        "status": "dismissed",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("org_id", org_id).eq("status", "pending").execute()
    return {"ok": True}


@router.delete("/{notif_id}")
def delete_notification(notif_id: str, org_id: str = Depends(get_current_org)):
    """Elimina una notificación."""
    _db().table("stream_notifications").delete().eq("id", notif_id).eq("org_id", org_id).execute()
    return {"ok": True}
