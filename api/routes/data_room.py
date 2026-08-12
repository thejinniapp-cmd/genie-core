"""api/routes/data_room.py — Data Room unificado conectado al Knowledge Graph."""
from fastapi import APIRouter, Depends
from supabase import create_client
import os
from typing import Any

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _node_id(kind: str, id: str) -> str:
    return f"{kind}:{id}"


def _fmt_dt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _resolve_agent_id(raw: str | None, agent_aliases: dict[str, dict]) -> str | None:
    if not raw:
        return None
    raw = str(raw).strip()
    if raw in agent_aliases:
        return agent_aliases[raw].get("id")
    # El campo leader_agent_id a veces usa el prefijo "agent-" (p. ej. "agent-cfo").
    if raw.startswith("agent-"):
        slug = raw[6:]
        if slug in agent_aliases:
            return agent_aliases[slug].get("id")
    return None


@router.get("/")
def get_data_room(org_id: str = Depends(get_current_org)):
    """
    Devuelve un grafo unificado de activos de la organización:
    fuentes RAG, archivos de Drive, workflows, streams y agentes,
    más las relaciones implícitas entre ellos.
    """
    db = _db()
    nodes: list[dict] = []
    edges: list[dict] = []

    # ── RAG sources (conocimiento gobernado) ─────────────────────────────────────
    rag_sources = (
        db.table("rag_sources")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    for r in rag_sources:
        nodes.append(
            {
                "id": _node_id("rag", r["id"]),
                "kind": "rag",
                "name": r.get("name") or "Fuente RAG",
                "source": "RAG",
                "status": "active",
                "created_at": _fmt_dt(r.get("created_at")),
                "updated_at": _fmt_dt(r.get("updated_at")),
                "meta": {
                    "source_type": r.get("source_type", "text"),
                    "scope": r.get("scope", "stream"),
                    "always_include": r.get("always_include", False),
                },
            }
        )
        if r.get("stream_id"):
            edges.append(
                {
                    "id": f"rag:{r['id']}->stream:{r['stream_id']}",
                    "source": _node_id("rag", r["id"]),
                    "target": _node_id("stream", r["stream_id"]),
                    "label": "contexto de",
                }
            )

    # ── Google Drive files (assets externos) ─────────────────────────────────────
    drive_files: list[dict] = []
    try:
        from core.connectors.executor import execute_connector_action

        drive_files = execute_connector_action(org_id, "drive", "list_files", {"max_results": 20}) or []
    except Exception:
        drive_files = []

    for f in drive_files:
        fid = f.get("id") or f.get("name")
        if not fid:
            continue
        nodes.append(
            {
                "id": _node_id("drive", fid),
                "kind": "drive",
                "name": f.get("name") or "Archivo Drive",
                "source": "DRIVE",
                "status": "connected",
                "created_at": _fmt_dt(f.get("modifiedTime")),
                "updated_at": None,
                "meta": {"mime_type": f.get("mimeType"), "web_link": f.get("webViewLink")},
            }
        )

    # ── Agentes (resueltos antes de workflows/streams para crear aristas) ───────
    agents = (
        db.table("agents")
        .select("*")
        .eq("org_id", org_id)
        .execute()
        .data
        or []
    )
    agent_aliases: dict[str, dict] = {}
    for a in agents:
        for key in (a.get("id"), a.get("name"), a.get("display_name"), f"agent-{a.get('name', '')}"):
            if key:
                agent_aliases[str(key).strip()] = a

    for a in agents:
        nodes.append(
            {
                "id": _node_id("agent", a["id"]),
                "kind": "agent",
                "name": a.get("display_name") or a.get("name") or "Agente",
                "source": "AGENT",
                "status": "active" if a.get("is_active") else "inactive",
                "created_at": _fmt_dt(a.get("created_at")),
                "updated_at": None,
                "meta": {"type": a.get("type")},
            }
        )

    # ── Workflow templates (procesos del negocio) ───────────────────────────────
    templates = (
        db.table("workflow_templates")
        .select("*")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )
    template_ids = [t["id"] for t in templates]

    steps: list[dict] = []
    if template_ids:
        steps = (
            db.table("workflow_steps")
            .select("*")
            .in_("template_id", template_ids)
            .execute()
            .data
            or []
        )

    steps_by_template: dict[str, list[dict]] = {}
    for s in steps:
        steps_by_template.setdefault(s["template_id"], []).append(s)

    for t in templates:
        nodes.append(
            {
                "id": _node_id("workflow", t["id"]),
                "kind": "workflow",
                "name": t.get("name") or "Flujo",
                "source": "WORKFLOW",
                "status": "active" if t.get("is_active") else "draft",
                "created_at": _fmt_dt(t.get("created_at")),
                "updated_at": None,
                "meta": {
                    "trigger_type": t.get("trigger_type"),
                    "description": t.get("description"),
                    "steps_count": len(steps_by_template.get(t["id"], [])),
                },
            }
        )
        for s in steps_by_template.get(t["id"], []):
            agent_uuid = _resolve_agent_id(s.get("agent_id"), agent_aliases)
            if agent_uuid:
                edges.append(
                    {
                        "id": f"workflow:{t['id']}->agent:{agent_uuid}",
                        "source": _node_id("workflow", t["id"]),
                        "target": _node_id("agent", agent_uuid),
                        "label": "usa agente",
                    }
                )

    # Workflow runs → relación workflow/stream
    if template_ids:
        runs = (
            db.table("workflow_runs")
            .select("template_id, stream_id")
            .in_("template_id", template_ids)
            .eq("org_id", org_id)
            .execute()
            .data
            or []
        )
        seen_pairs = set()
        for run in runs:
            tid = run.get("template_id")
            sid = run.get("stream_id")
            if not tid or not sid:
                continue
            pair = (tid, sid)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append(
                {
                    "id": f"workflow:{tid}->stream:{sid}",
                    "source": _node_id("workflow", tid),
                    "target": _node_id("stream", sid),
                    "label": "instanciado en",
                }
            )

    # ── Streams (conversaciones / casos de uso) ─────────────────────────────────
    streams = (
        db.table("streams")
        .select("*")
        .eq("org_id", org_id)
        .execute()
        .data
        or []
    )
    for s in streams:
        nodes.append(
            {
                "id": _node_id("stream", s["id"]),
                "kind": "stream",
                "name": s.get("name") or "Stream",
                "source": "STREAM",
                "status": s.get("status", "active"),
                "created_at": _fmt_dt(s.get("created_at")),
                "updated_at": None,
                "meta": {
                    "stream_type": s.get("stream_type"),
                    "leader_agent_id": s.get("leader_agent_id"),
                },
            }
        )
        leader_id = _resolve_agent_id(s.get("leader_agent_id"), agent_aliases)
        if leader_id:
            edges.append(
                {
                    "id": f"stream:{s['id']}->agent:{leader_id}",
                    "source": _node_id("stream", s["id"]),
                    "target": _node_id("agent", leader_id),
                    "label": "liderado por",
                }
            )

    # ── Deduplicar nodos y descartar aristas huérfanas ─────────────────────────
    seen: set[str] = set()
    deduped: list[dict] = []
    for n in nodes:
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        deduped.append(n)

    valid_edges = [e for e in edges if e["source"] in seen and e["target"] in seen]

    return {
        "nodes": deduped,
        "edges": valid_edges,
        "counts": {
            "rag": len([n for n in deduped if n["kind"] == "rag"]),
            "drive": len([n for n in deduped if n["kind"] == "drive"]),
            "workflow": len([n for n in deduped if n["kind"] == "workflow"]),
            "stream": len([n for n in deduped if n["kind"] == "stream"]),
            "agent": len([n for n in deduped if n["kind"] == "agent"]),
        },
    }
