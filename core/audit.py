"""
core/audit.py
=============
Audit log inmutable con hash encadenado.

Cada evento registra:
- qué pasó (action)
- quién lo hizo (actor: agente, usuario, sistema)
- en qué contexto (org, stream, job)
- el resultado (output)
- hash del evento anterior (encadenamiento tipo blockchain)

Esto permite:
- Auditorías en tiempo real
- Detección de patrones con IA
- Reconstrucción completa de cualquier proceso
- Trazabilidad para compliance
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any
from supabase import create_client

log = logging.getLogger("genie.audit")


def _supabase():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


def _hash_event(event: dict) -> str:
    """Genera el hash SHA-256 de un evento."""
    canonical = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _get_last_hash(org_id: str) -> str:
    """Obtiene el hash del último evento para encadenamiento."""
    try:
        res = (
            _supabase()
            .table("audit_log")
            .select("hash")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["hash"]
    except Exception:
        pass
    return "genesis"


def log_event(
    org_id: str,
    action: str,
    actor_type: str,          # "agent" | "user" | "system" | "external"
    actor_id: str,
    stream_id: str | None = None,
    job_id: str | None = None,
    input_data: Any = None,
    output_data: Any = None,
    status: str = "ok",       # "ok" | "error" | "pending" | "skipped"
    metadata: dict | None = None,
) -> str:
    """
    Registra un evento en el audit log.
    Devuelve el hash del evento creado.

    Uso:
        audit.log_event(
            org_id=org_id,
            action="rfq.search.completed",
            actor_type="agent",
            actor_id="agente_buscador",
            stream_id=stream_id,
            output_data={"top5": [...]},
            status="ok",
        )
    """
    prev_hash = _get_last_hash(org_id)

    event = {
        "org_id": org_id,
        "action": action,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "stream_id": stream_id,
        "job_id": job_id,
        "input_data": input_data,
        "output_data": output_data,
        "status": status,
        "metadata": metadata or {},
        "prev_hash": prev_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    event_hash = _hash_event(event)
    event["hash"] = event_hash

    try:
        _supabase().table("audit_log").insert(event).execute()
        log.debug(f"[audit] {action} by {actor_type}:{actor_id} → {status}")
    except Exception as e:
        log.error(f"[audit] Failed to write event: {e}")

    return event_hash


def get_events(
    org_id: str,
    stream_id: str | None = None,
    action_prefix: str | None = None,
    actor_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Consulta eventos del audit log con filtros opcionales."""
    query = (
        _supabase()
        .table("audit_log")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if stream_id:
        query = query.eq("stream_id", stream_id)
    if actor_id:
        query = query.eq("actor_id", actor_id)
    if action_prefix:
        query = query.like("action", f"{action_prefix}%")

    res = query.execute()
    return res.data or []


def verify_chain(org_id: str, limit: int = 1000) -> bool:
    """
    Verifica la integridad del audit log.
    Devuelve True si todos los hashes son válidos y están bien encadenados.
    """
    res = (
        _supabase()
        .table("audit_log")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    events = res.data or []

    prev_hash = "genesis"
    for event in events:
        stored_hash = event.pop("hash", None)
        computed = _hash_event(event)
        if computed != stored_hash:
            log.warning(f"[audit] Hash mismatch at event {event.get('created_at')}")
            return False
        if event.get("prev_hash") != prev_hash:
            log.warning(f"[audit] Chain broken at event {event.get('created_at')}")
            return False
        prev_hash = stored_hash
        event["hash"] = stored_hash  # restaurar

    return True
