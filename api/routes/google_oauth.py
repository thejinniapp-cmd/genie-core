"""
api/routes/google_oauth.py
Google OAuth 2.0 — un solo flujo conecta Gmail, Drive, Docs, Sheets, Slides.
"""
import os
import json
import secrets
import time
import urllib.parse
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()

# state nonce -> (org_id, expira). In-memory: válido para un solo worker (dev).
# Para multi-worker mover a tabla/Redis.
_OAUTH_STATES: dict = {}
_STATE_TTL_SECONDS = 600


def _new_state(org_id: str) -> str:
    now = time.time()
    for nonce, (_, exp) in list(_OAUTH_STATES.items()):
        if exp < now:
            _OAUTH_STATES.pop(nonce, None)
    nonce = secrets.token_urlsafe(32)
    _OAUTH_STATES[nonce] = (org_id, now + _STATE_TTL_SECONDS)
    return nonce


def _pop_state(nonce: str):
    entry = _OAUTH_STATES.pop(nonce, None)
    if not entry:
        return None
    org_id, expires_at = entry
    return org_id if time.time() < expires_at else None

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/calendar",
    "openid",
    "email",
    "profile",
]

def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

def _cfg():
    return {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
    }


@router.get("/auth-url")
def get_auth_url(org_id: str = Depends(get_current_org)):
    """Genera la URL de consentimiento de Google. El frontend redirige aquí."""
    cfg = _cfg()
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",          # Siempre pide refresh_token
        "state": _new_state(org_id),  # nonce de un solo uso; el org_id se recupera en el callback
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return {"url": url}


@router.get("/callback")
def oauth_callback(code: str, state: str):
    """
    Google redirige aquí con el código de autorización.
    Intercambiamos por tokens y los guardamos en la DB.
    """
    cfg = _cfg()
    org_id = _pop_state(state)
    if not org_id:
        raise HTTPException(status_code=400, detail="Estado OAuth inválido o expirado. Inicia la conexión de nuevo.")

    # Intercambiar código por tokens
    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code",
    })

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Error obteniendo tokens de Google. Intenta de nuevo.")

    tokens = resp.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not refresh_token:
        raise HTTPException(status_code=400, detail="No se obtuvo refresh_token. Revoca el acceso en myaccount.google.com e intenta de nuevo.")

    # Obtener info del usuario de Google
    user_resp = httpx.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={
        "Authorization": f"Bearer {access_token}"
    })
    user_info = user_resp.json() if user_resp.status_code == 200 else {}

    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": tokens.get("token_type", "Bearer"),
        "scope": tokens.get("scope", ""),
        "google_email": user_info.get("email", ""),
        "google_name": user_info.get("name", ""),
    }

    db = _db()

    # Guardar un conector por cada servicio de Google Workspace
    google_services = ["gmail", "drive", "sheets", "docs", "slides", "calendar"]
    for service in google_services:
        existing = db.table("connectors").select("id").eq("org_id", org_id).eq("connector_type", service).execute()
        if existing.data:
            db.table("connectors").update({
                "status": "connected",
                "credentials": credentials,
            }).eq("org_id", org_id).eq("connector_type", service).execute()
        else:
            db.table("connectors").insert({
                "org_id": org_id,
                "connector_type": service,
                "status": "connected",
                "credentials": credentials,
            }).execute()

    # Redirigir de vuelta al frontend con éxito
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    return RedirectResponse(url=f"{frontend_url}?google_connected=1")


@router.get("/check-permissions")
def check_google_permissions(org_id: str = Depends(get_current_org)):
    """Prueba real: intenta listar archivos en Drive para verificar que el token tiene los scopes correctos."""
    db = _db()
    res = db.table("connectors").select("credentials").eq("org_id", org_id).eq("connector_type", "drive").eq("status", "connected").execute()
    if not res.data:
        return {"ok": True}  # no hay drive configurado, no mostrar error
    creds = res.data[0].get("credentials", {})
    access_token = creds.get("access_token", "")
    try:
        r = httpx.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"pageSize": 1},
            timeout=10,
        )
        if r.status_code == 403:
            raise HTTPException(status_code=403, detail="Drive access forbidden — token missing scopes")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": True}  # error de red, no bloquear UI
