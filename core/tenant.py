"""
core/tenant.py
==============
Resolución de credenciales y configuración por organización (tenant).

Toda la lógica de negocio usa:
    creds = await tenant.get_credentials(org_id, "gmail")
    config = await tenant.get_config(org_id)

Nunca os.environ directamente en workers o agentes.
"""

import os
import json
import logging
from typing import Any
from functools import lru_cache
from supabase import create_client, Client

log = logging.getLogger("genie.tenant")


def _supabase() -> Client:
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


# ── Organización ──────────────────────────────────────────────────────────────

async def get_org(org_id: str) -> dict:
    """Devuelve los datos de una organización."""
    res = _supabase().table("organizations").select("*").eq("id", org_id).single().execute()
    if not res.data:
        raise ValueError(f"Organization not found: {org_id}")
    return res.data


async def get_config(org_id: str) -> dict:
    """
    Devuelve la configuración general del tenant.
    Incluye modelos preferidos, límites, autonomía global, etc.
    """
    res = _supabase().table("org_config").select("*").eq("org_id", org_id).single().execute()
    return res.data or {}


# ── Credenciales de conectores ────────────────────────────────────────────────

async def get_credentials(org_id: str, connector: str) -> dict:
    """
    Devuelve las credenciales de un conector para una organización.

    Uso:
        creds = await get_credentials(org_id, "gmail")
        token = creds["access_token"]

    Las credenciales están cifradas en DB y se descifran aquí.
    """
    res = (
        _supabase()
        .table("connectors")
        .select("credentials, status, config")
        .eq("org_id", org_id)
        .eq("connector_type", connector)
        .eq("status", "connected")
        .single()
        .execute()
    )
    if not res.data:
        raise ValueError(f"Connector '{connector}' not configured for org {org_id}")

    raw = res.data.get("credentials", {})
    # TODO: descifrar con KMS / Supabase Vault antes de devolver
    return raw if isinstance(raw, dict) else json.loads(raw)


async def list_connectors(org_id: str) -> list[dict]:
    """Lista todos los conectores configurados para una organización."""
    res = (
        _supabase()
        .table("connectors")
        .select("connector_type, status, config, updated_at")
        .eq("org_id", org_id)
        .execute()
    )
    return res.data or []


async def connector_is_active(org_id: str, connector: str) -> bool:
    """Verifica rápidamente si un conector está activo sin traer credenciales."""
    res = (
        _supabase()
        .table("connectors")
        .select("status")
        .eq("org_id", org_id)
        .eq("connector_type", connector)
        .single()
        .execute()
    )
    return bool(res.data and res.data.get("status") == "connected")


# ── Infraestructura por stream ────────────────────────────────────────────────

async def get_infra(org_id: str, stream_id: str | None = None) -> dict:
    """
    Devuelve la configuración de infraestructura.
    Si stream_id se pasa, busca infra específica del stream.
    Si no, devuelve la infra de la organización.

    Permite que cada stream use su propio Railway/Supabase/etc.
    """
    if stream_id:
        res = (
            _supabase()
            .table("stream_infra")
            .select("*")
            .eq("org_id", org_id)
            .eq("stream_id", stream_id)
            .single()
            .execute()
        )
        if res.data:
            return res.data

    # Fallback a infra de la organización
    res = (
        _supabase()
        .table("org_infra")
        .select("*")
        .eq("org_id", org_id)
        .single()
        .execute()
    )
    return res.data or {}


# ── Modelo de IA por agente ───────────────────────────────────────────────────

async def get_model(org_id: str, agent_id: str) -> str:
    """
    Devuelve el modelo de IA configurado para un agente.
    Fallback: modelo preferido del org → modelo default del sistema.
    """
    # 1. Configuración específica del agente
    res = (
        _supabase()
        .table("agents")
        .select("model_id")
        .eq("org_id", org_id)
        .eq("id", agent_id)
        .single()
        .execute()
    )
    if res.data and res.data.get("model_id"):
        return res.data["model_id"]

    # 2. Modelo preferido de la organización
    cfg = await get_config(org_id)
    if cfg.get("default_model"):
        return cfg["default_model"]

    # 3. Default del sistema
    return os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-sonnet-4-5")


# ── Usuario ───────────────────────────────────────────────────────────────────

async def get_user(user_id: str) -> dict:
    """Devuelve los datos de un usuario."""
    res = _supabase().table("users").select("*").eq("id", user_id).single().execute()
    if not res.data:
        raise ValueError(f"User not found: {user_id}")
    return res.data


async def get_user_permissions(user_id: str, org_id: str) -> dict:
    """
    Devuelve los permisos de un usuario dentro de una organización.
    Incluye rol, streams accesibles, acciones permitidas.
    """
    res = (
        _supabase()
        .table("org_members")
        .select("role, permissions, stream_access")
        .eq("user_id", user_id)
        .eq("org_id", org_id)
        .single()
        .execute()
    )
    return res.data or {"role": "viewer", "permissions": [], "stream_access": []}


async def can_approve(user_id: str, org_id: str, action: str) -> bool:
    """
    Verifica si un usuario puede aprobar una acción específica.
    Usado por el sistema human-in-the-loop.
    """
    perms = await get_user_permissions(user_id, org_id)
    permissions = perms.get("permissions", [])
    return action in permissions or "admin" in permissions or perms.get("role") == "owner"
