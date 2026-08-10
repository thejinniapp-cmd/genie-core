"""api/routes/connectors.py — gestión de conectores por org"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import os
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class ConnectorCreate(BaseModel):
    connector_type: str
    credentials: dict = {}
    config: dict = {}


def _strip_credentials(row: dict) -> dict:
    creds = row.pop("credentials", None) or {}
    row["has_credentials"] = bool(creds)
    return row


# ── Validación real de credenciales ──────────────────────────────────────────

def _validate_hubspot(credentials: dict) -> Optional[str]:
    token = (credentials.get("access_token") or "").strip()
    if not token:
        return "Falta el access token de HubSpot"
    import httpx
    try:
        r = httpx.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            params={"limit": 1, "properties": "firstname,lastname,email"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except httpx.RequestError as e:
        return f"No se pudo contactar a HubSpot: {e}"
    if r.status_code == 401:
        return "Token de HubSpot inválido o expirado"
    if r.status_code >= 400:
        return f"HubSpot respondió con error {r.status_code}"
    return None


def _validate_pipedrive(credentials: dict) -> Optional[str]:
    token = (credentials.get("api_token") or "").strip()
    if not token:
        return "Falta el API token de Pipedrive"
    import httpx
    try:
        r = httpx.get(
            "https://api.pipedrive.com/v1/users/me",
            params={"api_token": token},
            timeout=20,
        )
    except httpx.RequestError as e:
        return f"No se pudo contactar a Pipedrive: {e}"
    if r.status_code == 401:
        return "API token de Pipedrive inválido"
    if r.status_code >= 400:
        return f"Pipedrive respondió con error {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return "Pipedrive no devolvió una respuesta válida"
    if not data.get("success"):
        error = data.get("error") or data.get("message") or "Error desconocido de Pipedrive"
        return f"Pipedrive: {error}"
    return None


def _validate_odoo(credentials: dict) -> Optional[str]:
    url = (credentials.get("url") or "").strip()
    db = (credentials.get("database") or "").strip()
    username = (credentials.get("username") or "").strip()
    password = (credentials.get("api_key") or "").strip()
    if not all([url, db, username, password]):
        return "Faltan datos de Odoo (URL, base de datos, usuario o API key)"
    url = url.rstrip("/")
    rpc_url = f"{url}/jsonrpc"
    import httpx
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "common",
            "method": "authenticate",
            "args": [db, username, password, {}],
        },
        "id": 1,
    }
    try:
        r = httpx.post(
            rpc_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
            follow_redirects=False,
        )
    except httpx.RequestError as e:
        return f"No se pudo contactar a Odoo: {e}"
    if r.status_code in (301, 302, 307, 308):
        return "La URL de Odoo redirige; verifica que sea el dominio raíz de tu instancia (sin /web/login)"
    if r.status_code >= 400:
        return f"Odoo respondió con error {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return "Odoo no devolvió JSON válido; verifica la URL y que el JSON-RPC esté habilitado"
    if data.get("error"):
        msg = data["error"].get("message", "Error de Odoo")
        return f"Odoo: {msg}"
    uid = data.get("result")
    if not isinstance(uid, int) or uid == 0:
        return "Credenciales de Odoo inválidas"
    return None


def _validate_credentials(connector_type: str, credentials: dict) -> Optional[str]:
    """Devuelve None si OK, o un mensaje de error."""
    if connector_type == "hubspot":
        return _validate_hubspot(credentials)
    if connector_type == "pipedrive":
        return _validate_pipedrive(credentials)
    if connector_type == "odoo":
        return _validate_odoo(credentials)
    # Para otros conectores la validación es no-op por ahora
    return None


def _maybe_auto_sync(org_id: str, connector_type: str, credentials: dict):
    """Arranca sync en segundo plano cuando un conector con proveedor se conecta."""
    from core.sync.engine import PROVIDERS, run_sync
    if connector_type not in PROVIDERS:
        return
    import threading
    import logging
    log = logging.getLogger("genie.connectors")

    def _run():
        try:
            log.info(f"[connectors] Auto-sync {connector_type} for org {org_id}")
            run_sync(org_id, connector_type, credentials)
        except Exception as e:
            log.warning(f"[connectors] Auto-sync {connector_type} failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/")
def list_connectors(org_id: str = Depends(get_current_org)):
    rows = _db().table("connectors").select("*").eq("org_id", org_id).execute().data or []
    return [_strip_credentials(r) for r in rows]


@router.post("/")
def connect(body: ConnectorCreate, org_id: str = Depends(get_current_org)):
    error = _validate_credentials(body.connector_type, body.credentials) if body.credentials else None
    if error:
        raise HTTPException(400, error)

    from datetime import datetime, timezone
    res = _db().table("connectors").upsert({
        "org_id": org_id,
        "connector_type": body.connector_type,
        "credentials": body.credentials,
        "config": body.config,
        "status": "connected",
        "last_tested_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="org_id,connector_type").execute()

    # Disparar sincronización automática en segundo plano para conectores soportados
    _maybe_auto_sync(org_id, body.connector_type, body.credentials)

    return _strip_credentials(res.data[0]) if res.data else {}


@router.post("/{connector_type}/test")
def test_connector(connector_type: str, org_id: str = Depends(get_current_org)):
    res = _db().table("connectors").select("*").eq("org_id", org_id).eq("connector_type", connector_type).single().execute()
    if not res.data:
        raise HTTPException(404, "Connector not found")
    row = res.data
    error = _validate_credentials(connector_type, row.get("credentials") or {})
    if error:
        raise HTTPException(400, error)

    from datetime import datetime, timezone
    _db().table("connectors").update({
        "status": "connected",
        "last_tested_at": datetime.now(timezone.utc).isoformat(),
    }).eq("org_id", org_id).eq("connector_type", connector_type).execute()
    return {"status": "ok", "connector_type": connector_type}


@router.delete("/{connector_type}")
def disconnect(connector_type: str, org_id: str = Depends(get_current_org)):
    _db().table("connectors").delete().eq("org_id", org_id).eq("connector_type", connector_type).execute()
    return {"status": "disconnected"}


@router.post("/{connector_type}/sync")
def sync_connector(connector_type: str, org_id: str = Depends(get_current_org)):
    """Ejecuta una sincronización funcional desde el sistema externo hacia Genie."""
    res = _db().table("connectors").select("*").eq("org_id", org_id).eq("connector_type", connector_type).limit(1).execute()
    row = res.data[0] if res.data else None
    if not row or row.get("status") not in ("connected", "active"):
        raise HTTPException(400, "Conector no conectado")
    try:
        from core.sync.engine import run_sync
        return run_sync(org_id, connector_type, row.get("credentials") or {})
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/{connector_type}/syncs")
def list_syncs(connector_type: str, org_id: str = Depends(get_current_org)):
    """Historial de sincronizaciones del conector."""
    rows = _db().table("audit_log").select("*") \
        .eq("org_id", org_id) \
        .eq("action", "connector_sync") \
        .eq("metadata->>connector_type", connector_type) \
        .order("created_at", desc=True) \
        .limit(20).execute().data or []
    return rows


@router.get("/drive/files")
def list_drive_files(
    q: str = "",
    page_token: str = "",
    org_id: str = Depends(get_current_org),
):
    """Lista/busca archivos de Google Drive para el picker del chat."""
    from core.connectors.executor import execute_connector_action
    try:
        if q:
            result = execute_connector_action(org_id, "drive", "search_files", {"name": q})
        else:
            result = execute_connector_action(org_id, "drive", "list_files", {"max_results": 20})
        return result
    except Exception as e:
        raise HTTPException(400, str(e))
