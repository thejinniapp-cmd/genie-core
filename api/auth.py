"""api/auth.py — resolución de identidad y organización por request.

Modo gradual:
- AUTH_REQUIRED=false (default, dev): acepta Bearer JWT si viene; si no, usa X-Org-Id.
- AUTH_REQUIRED=true: Bearer JWT obligatorio; la org se deriva del usuario
  autenticado (user_organizations). Si además viene X-Org-Id, se valida membresía.
"""
import os
import logging
from fastapi import Header, HTTPException
from supabase import create_client

log = logging.getLogger("genie.auth")

AUTH_REQUIRED = os.environ.get("AUTH_REQUIRED", "false").lower() == "true"

_client = None


def _db():
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _client


def get_current_org(
    authorization: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> str:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    if not token:
        if AUTH_REQUIRED:
            raise HTTPException(401, "Falta token de autenticación")
        if not x_org_id:
            raise HTTPException(400, "Falta header X-Org-Id")
        return x_org_id

    try:
        user_res = _db().auth.get_user(token)
        user_id = user_res.user.id
    except Exception:
        if AUTH_REQUIRED or not x_org_id:
            raise HTTPException(401, "Token inválido o expirado")
        return x_org_id

    q = _db().table("user_organizations").select("org_id").eq("user_id", user_id)
    if x_org_id:
        q = q.eq("org_id", x_org_id)
    res = q.limit(1).execute()
    if not res.data:
        if x_org_id:
            raise HTTPException(403, "El usuario no pertenece a esa organización")
        raise HTTPException(403, "El usuario no tiene organización asignada")
    return res.data[0]["org_id"]
