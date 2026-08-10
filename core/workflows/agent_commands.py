"""core/workflows/agent_commands.py — Bus de comandos del Chief of Staff a los agents.

Usa la tabla existente `messages` como cola de comandos durable. Los mensajes con
role='command' y author='chief_of_staff' representan órdenes para los agentes de
cada módulo. El scheduler de cada agente lee los pendientes y los ejecuta.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client
import os

log = logging.getLogger("genie.agent_commands")


COMMANDS_BY_AGENT = {
    "collections": "run_overdue",
    "inventory": "run_low_stock",
    "crm": "run_stale_deals",
    "accounting": "run_monthly_close",
}


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _command_label(agent_key: str, command: str) -> str:
    labels = {
        ("collections", "run_overdue"): "Ejecutar cobranza de facturas vencidas",
        ("inventory", "run_low_stock"): "Revisar stock bajo y generar órdenes de compra",
        ("crm", "run_stale_deals"): "Reactivar deals estancados",
        ("accounting", "run_monthly_close"): "Cerrar período contable mensual",
    }
    return labels.get((agent_key, command), f"{command} para {agent_key}")


def _find_stream(org_id: str) -> Optional[str]:
    try:
        rows = (
            _db()
            .table("streams")
            .select("id")
            .eq("org_id", org_id)
            .order("created_at")
            .limit(1)
            .execute()
            .data
            or []
        )
        return rows[0]["id"] if rows else None
    except Exception as e:
        log.warning(f"[agent_commands] Could not find stream: {e}")
        return None


def _pending_command_exists(org_id: str, agent_key: str, command: str) -> bool:
    try:
        rows = (
            _db()
            .table("messages")
            .select("id")
            .eq("org_id", org_id)
            .eq("author", "chief_of_staff")
            .eq("role", "command")
            .eq("metadata->>agent_key", agent_key)
            .eq("metadata->>command", command)
            .neq("metadata->>processed", "true")
            .limit(1)
            .execute()
            .data
            or []
        )
        return len(rows) > 0
    except Exception as e:
        log.warning(f"[agent_commands] Could not check pending command: {e}")
        return False


def post_command(
    org_id: str,
    agent_key: str,
    command: str,
    stream_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    source: str = "chief_of_staff",
) -> Optional[Dict[str, Any]]:
    """
    Publica un comando para un agente en la cola de mensajes.
    Evita duplicados: si ya hay un comando pendiente para el mismo agente/comando,
    retorna el existente.
    """
    if agent_key not in COMMANDS_BY_AGENT or COMMANDS_BY_AGENT[agent_key] != command:
        log.warning(f"[agent_commands] Unknown command {command} for agent {agent_key}")
        return None

    if _pending_command_exists(org_id, agent_key, command):
        log.info(f"[agent_commands] Pending command already exists for {agent_key}/{command}")
        return None

    stream_id = stream_id or _find_stream(org_id)
    if not stream_id:
        log.warning(f"[agent_commands] No stream found for org {org_id}")
        return None

    try:
        res = _db().table("messages").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "role": "command",
            "author": "chief_of_staff",
            "content": {
                "text": f"Orden del Chief of Staff: {_command_label(agent_key, command)}",
                "command": command,
                "target_agent": agent_key,
            },
            "metadata": {
                "source": source,
                "agent_key": agent_key,
                "command": command,
                "payload": payload or {},
                "processed": "false",
            },
        }).execute().data
        return res[0] if res else None
    except Exception as e:
        log.warning(f"[agent_commands] Could not post command {agent_key}/{command}: {e}")
        return None


def get_pending_commands(org_id: str, agent_key: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        q = (
            _db()
            .table("messages")
            .select("*")
            .eq("org_id", org_id)
            .eq("author", "chief_of_staff")
            .eq("role", "command")
            .neq("metadata->>processed", "true")
        )
        if agent_key:
            q = q.eq("metadata->>agent_key", agent_key)
        rows = q.order("created_at").execute().data or []
        return rows
    except Exception as e:
        log.warning(f"[agent_commands] Could not get pending commands: {e}")
        return []


def mark_command_processed(
    message_id: str,
    status: str = "completed",
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
):
    try:
        row = _db().table("messages").select("metadata").eq("id", message_id).maybe_single().execute().data
        metadata = row.get("metadata") or {} if row else {}
        metadata["processed"] = "true"
        metadata["status"] = status
        metadata["processed_at"] = datetime.now(timezone.utc).isoformat()
        if result is not None:
            metadata["result"] = result
        if error is not None:
            metadata["error"] = error
        _db().table("messages").update({"metadata": metadata}).eq("id", message_id).execute()
    except Exception as e:
        log.warning(f"[agent_commands] Could not mark command {message_id} processed: {e}")


def list_agent_keys() -> List[str]:
    return list(COMMANDS_BY_AGENT.keys())
