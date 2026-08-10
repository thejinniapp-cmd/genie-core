"""
core/connectors/executor.py
===========================
Despacha acciones a los conectores configurados.

Cuando un agente quiere ejecutar una acción (ej: enviar un email),
llama a execute_connector_action() y este módulo:
1. Verifica que el conector esté activo para el org
2. Obtiene las credenciales del tenant
3. Despacha a la implementación del conector
4. Devuelve el resultado

Nuevos conectores se agregan implementando la interfaz BaseConnector
y registrándolos en CONNECTOR_REGISTRY.
"""

import os
import json
import hashlib
import logging
import httpx
from datetime import datetime, timezone
from typing import Any
from supabase import create_client

def _get_db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

log = logging.getLogger("genie.connectors")


# ── Registro de conectores ────────────────────────────────────────────────────
# Cada entrada: "connector_type" → clase del conector

CONNECTOR_REGISTRY: dict = {}


def register_connector(connector_type: str):
    """Decorador para registrar un conector."""
    def decorator(cls):
        CONNECTOR_REGISTRY[connector_type] = cls
        log.info(f"[connectors] Registered: {connector_type}")
        return cls
    return decorator


# ── Ejecutor principal ────────────────────────────────────────────────────────

def execute_connector_action(
    org_id: str,
    connector_type: str,
    action: str,
    params: dict,
    stream_id: str = None,
    job_id: str = None,
) -> Any:
    """
    Ejecuta una acción en un conector para una organización.

    Uso:
        result = execute_connector_action(
            org_id, "gmail", "send_email",
            {"to": "...", "subject": "...", "body": "..."}
        )
    """
    # NotebookLM usa funciones de módulo directamente (no clase + credentials)
    if connector_type == "notebooklm":
        return _execute_notebooklm_action(org_id, action, params, stream_id=stream_id)

    if connector_type not in CONNECTOR_REGISTRY:
        raise ValueError(f"Connector '{connector_type}' not registered. Available: {list(CONNECTOR_REGISTRY.keys())}")

    # Obtener credenciales directamente (síncrono)
    db = _get_db()
    res = db.table("connectors").select("credentials,status") \
        .eq("org_id", org_id).eq("connector_type", connector_type).single().execute()
    if not res.data or res.data.get("status") not in ("connected", "active"):
        raise ValueError(f"Connector '{connector_type}' is not configured for this organization")
    credentials = res.data.get("credentials") or {}
    if credentials.get("refresh_token") and connector_type in ("gmail", "drive", "sheets", "docs", "slides", "calendar"):
        credentials = _refresh_google_token_if_needed(org_id, connector_type, credentials)

    # Instanciar y ejecutar
    connector_cls = CONNECTOR_REGISTRY[connector_type]
    connector = connector_cls(credentials, org_id=org_id)

    if not hasattr(connector, action):
        raise ValueError(f"Action '{action}' not found in connector '{connector_type}'")

    log.info(f"[connectors] {connector_type}.{action} for org {org_id}")

    # Ejecutar la acción
    status = "success"
    result = None
    error_msg = None
    try:
        result = getattr(connector, action)(**params)
    except Exception as e:
        status = "error"
        error_msg = str(e)
        log.error(f"[connectors] {connector_type}.{action} failed: {e}")

    # Registrar en audit_log (siempre, éxito o error)
    try:
        _write_audit_log(
            org_id=org_id,
            stream_id=stream_id,
            job_id=job_id,
            connector_type=connector_type,
            action=action,
            params=params,
            result=result,
            status=status,
            error=error_msg,
        )
    except Exception as audit_err:
        log.warning(f"[connectors] audit_log write failed: {audit_err}")

    if status == "error":
        raise Exception(error_msg)

    return result


# ── Clase base para conectores ────────────────────────────────────────────────

class BaseConnector:
    """
    Clase base para todos los conectores.

    Cada conector implementa sus acciones como métodos.
    Las credenciales se inyectan en __init__.

    Ejemplo:
        @register_connector("gmail")
        class GmailConnector(BaseConnector):
            def send_email(self, to, subject, body):
                # usar self.credentials["access_token"]
                ...
    """

    def __init__(self, credentials: dict, org_id: str | None = None):
        self.credentials = credentials
        self.org_id = org_id

    def get_tools_schema(self) -> list[dict]:
        """
        Devuelve el schema de tools para el modelo de IA.
        Sobreescribir en cada conector.
        """
        return []


# ── Conectores built-in ───────────────────────────────────────────────────────
# Se importan aquí para que se registren automáticamente al importar este módulo

_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "key", "credential", "cookie", "auth")


def sanitize_params(params: dict) -> dict:
    """Redacta valores sensibles por nombre de campo (substring, case-insensitive), recursivo."""
    if not isinstance(params, dict):
        return params
    safe = {}
    for k, v in params.items():
        if any(part in str(k).lower() for part in _SENSITIVE_KEY_PARTS):
            safe[k] = "***"
        elif isinstance(v, dict):
            safe[k] = sanitize_params(v)
        else:
            safe[k] = v
    return safe


def _write_audit_log(org_id, stream_id, job_id, connector_type, action,
                     params, result, status, error=None):
    """Escribe un evento en audit_log con toda la info del conector ejecutado."""
    db = _get_db()

    # Sanitizar params — quitar campos sensibles
    safe_params = sanitize_params(params or {})

    # Descripción legible del evento
    action_labels = {
        "send_email": "📧 Email enviado",
        "create_draft": "📝 Borrador creado",
        "list_emails": "📬 Emails consultados",
        "search_emails": "🔍 Búsqueda en Gmail",
        "get_email": "📖 Email leído",
        "write_range": "✏️ Datos escritos en Sheets",
        "append_rows": "➕ Filas agregadas en Sheets",
        "read_range": "📊 Sheets consultado",
        "create_spreadsheet": "📊 Spreadsheet creado",
        "get_document": "📄 Doc leído",
        "create_document": "📄 Doc creado",
        "append_text": "✏️ Texto agregado a Doc",
        "list_files": "📁 Drive consultado",
        "search_files": "🔍 Búsqueda en Drive",
        "create_folder": "📁 Carpeta creada en Drive",
        "share_file": "🔗 Archivo compartido en Drive",
        "send_message": "💬 Mensaje enviado",
        "get_channel_history": "📜 Historial consultado",
        "list_customers": "👥 Clientes consultados",
        "list_payments": "💳 Pagos consultados",
        "get_balance": "💰 Balance consultado",
        "list_contacts": "👤 Contactos consultados",
        "create_contact": "👤 Contacto creado",
        "list_deals": "🤝 Deals consultados",
    }
    label = action_labels.get(action, f"⚡ {connector_type}.{action}")

    # Resumen del resultado para mostrar en UI
    summary = {}
    if result and isinstance(result, dict):
        for k in ("to", "subject", "sent", "updated", "appended", "title",
                  "spreadsheet_id", "document_id", "folder_id", "chat_id"):
            if k in result:
                summary[k] = result[k]
    elif result and isinstance(result, list):
        summary["count"] = len(result)

    # Hash encadenado para integridad del log
    prev = db.table("audit_log").select("hash").eq("org_id", org_id) \
              .order("created_at", desc=True).limit(1).execute()
    prev_hash = prev.data[0]["hash"] if prev.data else "genesis"
    payload = f"{org_id}:{connector_type}:{action}:{datetime.now(timezone.utc).isoformat()}"
    current_hash = hashlib.sha256(f"{prev_hash}:{payload}".encode()).hexdigest()

    db.table("audit_log").insert({
        "org_id": org_id,
        "stream_id": stream_id,
        "job_id": job_id,
        "action": f"{connector_type}.{action}",
        "actor_type": "connector",
        "actor_id": connector_type,
        "status": status,
        "input_data": safe_params,
        "output_data": summary,
        "metadata": {
            "label": label,
            "connector": connector_type,
            "action": action,
            "error": error,
        },
        "prev_hash": prev_hash,
        "hash": current_hash,
    }).execute()

    log.info(f"[audit] {label} | org={org_id} stream={stream_id} status={status}")


def _refresh_google_token_if_needed(org_id: str, connector_type: str, credentials: dict) -> dict:
    """Refresca el access_token de Google usando el refresh_token si es necesario."""
    try:
        # Intentar una llamada simple para ver si el token es válido
        test = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {credentials['access_token']}"},
            timeout=5,
        )
        if test.status_code == 200:
            return credentials  # Token válido, no refrescar

        # Token expirado — refrescar
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": credentials["refresh_token"],
            "grant_type": "refresh_token",
        }, timeout=10)

        if resp.status_code == 200:
            new_tokens = resp.json()
            credentials = {**credentials, "access_token": new_tokens["access_token"]}
            # Guardar token nuevo en DB para todos los conectores Google
            from supabase import create_client
            db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
            for svc in ("gmail", "drive", "sheets", "docs", "slides", "calendar"):
                db.table("connectors").update({"credentials": credentials}) \
                  .eq("org_id", org_id).eq("connector_type", svc).execute()
            log.info(f"[connectors] Google token refreshed for org {org_id}")
        else:
            log.warning(f"[connectors] Token refresh failed: {resp.text}")
    except Exception as e:
        log.warning(f"[connectors] Token refresh error: {e}")
    return credentials


def _execute_notebooklm_action(org_id: str, action: str, params: dict, stream_id: str = None) -> Any:
    """Delega acciones de NotebookLM a las funciones del módulo core.connectors.notebooklm."""
    from core.connectors import notebooklm as nlm
    action_map = {
        "listar": nlm.listar_notebooks,
        "crear": nlm.crear_notebook,
        "agregar_fuente": nlm.agregar_fuente,
        "chatear": nlm.chatear,
        "generar": nlm.generar_contenido,
    }
    fn = action_map.get(action)
    if not fn:
        raise ValueError(f"NotebookLM: acción '{action}' no existe. Disponibles: {list(action_map.keys())}")
    if action == "generar" and stream_id:
        return fn(org_id=org_id, stream_id=stream_id, **params)
    return fn(org_id=org_id, **params)


def _load_builtin_connectors():
    for module_name in (
        "gmail",
        "google_sheets",
        "google_docs",
        "google_drive",
        "google_slides",
        "google_calendar",
        "telegram_connector",
        "slack_connector",
        "stripe_connector",
        "hubspot_connector",
        "pipedrive_connector",
        "odoo_connector",
    ):
        try:
            __import__(f"core.connectors.{module_name}")
        except ImportError as e:
            log.warning(f"[connectors] Built-in connector '{module_name}' not loaded: {e}")

_load_builtin_connectors()
