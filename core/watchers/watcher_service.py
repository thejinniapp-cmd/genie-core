"""
core/watchers/watcher_service.py
=================================
Servicio de watchers que corre en background dentro de uvicorn.
Detecta eventos externos (nuevos correos, cambios en Drive, etc.)
y crea notificaciones en stream_notifications.

Cada watcher es independiente y se ejecuta en su propio thread con
un intervalo configurable.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from supabase import create_client

log = logging.getLogger("genie.watchers")

# Intervalo de polling por defecto (segundos)
GMAIL_POLL_INTERVAL = 60      # cada 1 minuto
DRIVE_POLL_INTERVAL = 120     # cada 2 minutos

_watcher_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _get_db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ── Crear notificación ────────────────────────────────────────────────────────

def create_notification(
    org_id: str,
    stream_id: Optional[str],
    notif_type: str,
    title: str,
    message: str,
    source: str,
    metadata: dict = None,
):
    """Inserta una notificación en stream_notifications."""
    try:
        db = _get_db()
        db.table("stream_notifications").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "type": notif_type,          # info | alert | approval_required
            "message": f"{title}\n{message}",
            "status": "pending",
            "metadata": {
                "title": title,
                "body": message,
                "source": source,        # gmail | drive | sheets | docs | slides
                **(metadata or {}),
            },
        }).execute()
        log.info(f"[notif] {source} → {title[:60]} | org={org_id}")
    except Exception as e:
        log.warning(f"[notif] Failed to create notification: {e}")


# ── Watcher state helpers ─────────────────────────────────────────────────────

def _get_watcher_state(db, org_id: str, connector_type: str) -> dict:
    """Lee el estado del watcher desde credentials del conector."""
    try:
        res = db.table("connectors").select("credentials").eq("org_id", org_id).eq("connector_type", connector_type).single().execute()
        if res.data:
            creds = res.data.get("credentials") or {}
            return creds.get("_watcher_state", {})
    except Exception:
        pass
    return {}


def _save_watcher_state(db, org_id: str, connector_type: str, state: dict):
    """Guarda el estado del watcher en credentials del conector."""
    try:
        res = db.table("connectors").select("credentials").eq("org_id", org_id).eq("connector_type", connector_type).single().execute()
        if res.data:
            creds = res.data.get("credentials") or {}
            creds["_watcher_state"] = state
            db.table("connectors").update({"credentials": creds}).eq("org_id", org_id).eq("connector_type", connector_type).execute()
    except Exception as e:
        log.warning(f"[watchers] save_state failed for {connector_type}/{org_id}: {e}")


# ── Gmail Watcher ─────────────────────────────────────────────────────────────

def _refresh_google_token(org_id: str, creds: dict) -> dict:
    """Refresca el access_token de Google y lo guarda en todos los conectores Google."""
    import httpx
    try:
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": creds.get("refresh_token", ""),
            "grant_type": "refresh_token",
        }, timeout=10)
        if resp.status_code == 200:
            new_access = resp.json().get("access_token")
            creds = {**creds, "access_token": new_access}
            db2 = _get_db()
            for svc in ("gmail", "drive", "sheets", "docs", "slides"):
                db2.table("connectors").update({"credentials": creds}) \
                   .eq("org_id", org_id).eq("connector_type", svc).execute()
            log.info(f"[watchers] Google token refreshed for org {org_id}")
        else:
            log.warning(f"[watchers] Token refresh failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        log.warning(f"[watchers] Token refresh error: {e}")
    return creds


def _watch_gmail_for_org(db, org_id: str, stream_id: Optional[str]):
    """Verifica nuevos correos en Gmail para un org."""
    import httpx

    try:
        res = db.table("connectors").select("credentials,status").eq("org_id", org_id).eq("connector_type", "gmail").single().execute()
        if not res.data or res.data.get("status") not in ("connected", "active"):
            return
        creds = res.data.get("credentials") or {}
        access_token = creds.get("access_token")
        if not access_token:
            return

        # Refrescar token proactivamente si hay refresh_token
        if creds.get("refresh_token"):
            creds = _refresh_google_token(org_id, creds)
            access_token = creds.get("access_token", access_token)

        state = _get_watcher_state(db, org_id, "gmail")
        # Timestamp del último check (epoch segundos); si no existe, 2 horas atrás
        last_epoch = state.get("last_epoch")
        if not last_epoch:
            last_epoch = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())

        # Buscar todos los mensajes en INBOX recibidos después del último check
        # (no solo no-leídos — el usuario puede haberlos leído antes del ciclo)
        query = f"in:inbox after:{last_epoch}"
        resp = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": query, "maxResults": 10},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if resp.status_code in (401, 403):
            log.warning(f"[gmail_watcher] Auth error {resp.status_code} for org {org_id}")
            return

        now_epoch = int(datetime.now(timezone.utc).timestamp())
        seen_ids = set(state.get("seen_ids", []))
        new_seen = set(seen_ids)
        found = 0

        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            found = len(messages)

            for msg in messages[:5]:
                msg_id = msg.get("id")
                if not msg_id or msg_id in seen_ids:
                    continue
                new_seen.add(msg_id)

                msg_resp = httpx.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if msg_resp.status_code == 200:
                    msg_data = msg_resp.json()
                    hdrs = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                    subject = hdrs.get("Subject", "(Sin asunto)")
                    sender = hdrs.get("From", "Desconocido")
                    create_notification(
                        org_id=org_id,
                        stream_id=stream_id,
                        notif_type="info",
                        title=f"📧 Nuevo correo: {subject[:60]}",
                        message=f"De: {sender}",
                        source="gmail",
                        metadata={"msg_id": msg_id, "subject": subject, "from": sender},
                    )

        # Guardar estado: epoch actual + ids vistos (máximo 200 para no crecer indefinidamente)
        _save_watcher_state(db, org_id, "gmail", {
            "last_epoch": now_epoch,
            "seen_ids": list(new_seen)[-200:],
        })
        log.debug(f"[gmail_watcher] org={org_id} found={found} new={len(new_seen - seen_ids)}")

    except Exception as e:
        log.warning(f"[gmail_watcher] Error for org {org_id}: {e}")


# ── Drive Watcher ─────────────────────────────────────────────────────────────

def _watch_drive_for_org(db, org_id: str, stream_id: Optional[str]):
    """Detecta cambios en Google Drive para un org."""
    import httpx

    try:
        res = db.table("connectors").select("credentials,status").eq("org_id", org_id).eq("connector_type", "drive").single().execute()
        if not res.data or res.data.get("status") not in ("connected", "active"):
            return
        creds = res.data.get("credentials") or {}
        access_token = creds.get("access_token")
        if not access_token:
            return

        # Refrescar token proactivamente
        if creds.get("refresh_token"):
            creds = _refresh_google_token(org_id, creds)
            access_token = creds.get("access_token", access_token)

        state = _get_watcher_state(db, org_id, "drive")
        page_token = state.get("page_token")

        if not page_token:
            # Obtener page_token inicial
            resp = httpx.get(
                "https://www.googleapis.com/drive/v3/changes/startPageToken",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                new_token = resp.json().get("startPageToken")
                _save_watcher_state(db, org_id, "drive", {
                    "page_token": new_token,
                    "last_check": datetime.now(timezone.utc).isoformat(),
                })
                log.info(f"[drive_watcher] Initialized for org {org_id}, pageToken={new_token}")
            return

        # Obtener cambios desde el page_token
        resp = httpx.get(
            "https://www.googleapis.com/drive/v3/changes",
            params={
                "pageToken": page_token,
                "fields": "nextPageToken,newStartPageToken,changes(changeType,fileId,file(name,mimeType,modifiedTime,lastModifyingUser))",
                "includeRemoved": "false",
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if resp.status_code in (401, 403):
            log.warning(f"[drive_watcher] Auth error {resp.status_code} for org {org_id}")
            return
        if resp.status_code != 200:
            return

        data = resp.json()
        new_token = data.get("newStartPageToken") or data.get("nextPageToken") or page_token

        changes = data.get("changes", [])
        for change in changes[:5]:
            file_info = change.get("file", {})
            if not file_info:
                continue
            name = file_info.get("name", "Archivo")
            mime = file_info.get("mimeType", "")
            modifier = file_info.get("lastModifyingUser", {}).get("displayName", "Alguien")

            # Detectar tipo de archivo
            mime_icons = {
                "application/vnd.google-apps.spreadsheet": "📊",
                "application/vnd.google-apps.document": "📄",
                "application/vnd.google-apps.presentation": "📊",
                "application/vnd.google-apps.folder": "📁",
            }
            icon = mime_icons.get(mime, "📎")

            create_notification(
                org_id=org_id,
                stream_id=stream_id,
                notif_type="info",
                title=f"{icon} {name} fue modificado",
                message=f"Por: {modifier}",
                source="drive",
                metadata={
                    "file_id": change.get("fileId"),
                    "file_name": name,
                    "mime_type": mime,
                    "modifier": modifier,
                },
            )

        _save_watcher_state(db, org_id, "drive", {
            "page_token": new_token,
            "last_check": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        log.warning(f"[drive_watcher] Error for org {org_id}: {e}")


# ── Obtener stream principal de un org ────────────────────────────────────────

def _get_primary_stream(db, org_id: str) -> Optional[str]:
    """Retorna el stream_id principal del org (el más reciente)."""
    try:
        res = db.table("streams").select("id").eq("org_id", org_id).order("created_at").limit(1).execute()
        if res.data:
            return res.data[0]["id"]
    except Exception:
        pass
    return None


# ── Loop principal del watcher ────────────────────────────────────────────────

def _watcher_loop():
    """Loop que corre en background y llama a cada watcher periódicamente."""
    log.info("[watchers] Watcher service started")
    gmail_last_run: dict[str, float] = {}
    drive_last_run: dict[str, float] = {}

    while not _stop_event.is_set():
        try:
            db = _get_db()

            # Obtener todos los orgs con connectors activos
            res = db.table("connectors").select("org_id,connector_type").in_("status", ["connected", "active"]).in_("connector_type", ["gmail", "drive"]).execute()

            orgs_gmail = set()
            orgs_drive = set()
            for row in (res.data or []):
                if row["connector_type"] == "gmail":
                    orgs_gmail.add(row["org_id"])
                elif row["connector_type"] == "drive":
                    orgs_drive.add(row["org_id"])

            now = time.time()

            # Gmail watchers — un error en un tenant no afecta a los demás
            for org_id in orgs_gmail:
                last = gmail_last_run.get(org_id, 0)
                if now - last >= GMAIL_POLL_INTERVAL:
                    try:
                        stream_id = _get_primary_stream(db, org_id)
                        _watch_gmail_for_org(db, org_id, stream_id)
                    except Exception as e:
                        log.warning(f"[watchers] gmail error for org {org_id}: {e}")
                    gmail_last_run[org_id] = now

            # Drive watchers — idem
            for org_id in orgs_drive:
                last = drive_last_run.get(org_id, 0)
                if now - last >= DRIVE_POLL_INTERVAL:
                    try:
                        stream_id = _get_primary_stream(db, org_id)
                        _watch_drive_for_org(db, org_id, stream_id)
                    except Exception as e:
                        log.warning(f"[watchers] drive error for org {org_id}: {e}")
                    drive_last_run[org_id] = now

        except Exception as e:
            log.error(f"[watchers] Loop error: {e}")

        # Sleep en intervalos de 10s para poder detectar el stop_event
        _stop_event.wait(timeout=10)

    log.info("[watchers] Watcher service stopped")


# ── API pública ───────────────────────────────────────────────────────────────

def start_watcher_service():
    """Arranca el watcher service en un thread daemon."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        log.warning("[watchers] Already running")
        return
    _stop_event.clear()
    _watcher_thread = threading.Thread(target=_watcher_loop, name="genie-watchers", daemon=True)
    _watcher_thread.start()
    log.info("[watchers] Started")


def stop_watcher_service():
    """Detiene el watcher service."""
    _stop_event.set()
    if _watcher_thread:
        _watcher_thread.join(timeout=15)
    log.info("[watchers] Stopped")
