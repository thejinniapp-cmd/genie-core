"""
core/rag/context.py
===================
Sistema RAG con dos niveles:

1. Contexto global (org-level)   → aplica a TODOS los agentes
2. Contexto de stream            → aplica solo a ese stream

Los agentes llaman a get_context() y reciben el contexto combinado
ya listo para meter al system prompt o como tool de búsqueda.

Usa pgvector en Supabase para búsqueda semántica.
"""

import os
import json
import logging
from typing import Any
from supabase import create_client

log = logging.getLogger("genie.rag")


def _supabase():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


# ── Tipos de fuente ────────────────────────────────────────────────────────────

SOURCE_TYPES = {
    "rule":     "Regla operativa — siempre en contexto",
    "policy":   "Política de la empresa",
    "document": "Documento de referencia — búsqueda semántica",
    "url":      "URL indexada",
    "text":     "Texto libre",
    "faq":      "Pregunta frecuente",
}


# ── Agregar fuentes ───────────────────────────────────────────────────────────

def add_source(
    org_id: str,
    content: str,
    source_type: str,
    name: str,
    stream_id: str | None = None,     # None = global para toda la org
    metadata: dict | None = None,
    always_include: bool = False,     # True = va siempre al system prompt
) -> str:
    """
    Agrega una fuente al acervo del stream o de la organización.

    Si always_include=True, el contenido se incluye siempre en el
    system prompt del agente (útil para políticas y reglas cortas).

    Si always_include=False, se indexa para búsqueda semántica (RAG clásico).
    """
    record = {
        "org_id": org_id,
        "stream_id": stream_id,
        "name": name,
        "source_type": source_type,
        "content": content,
        "always_include": always_include,
        "metadata": metadata or {},
        "scope": "global" if stream_id is None else "stream",
    }

    res = _supabase().table("rag_sources").insert(record).execute()
    source_id = res.data[0]["id"] if res.data else None
    log.info(f"[rag] Added source '{name}' ({source_type}) scope={'global' if not stream_id else stream_id}")
    return source_id


def propagate_source(source_id: str, target_stream_ids: list[str]) -> int:
    """
    Propaga una fuente de un stream a otros streams.
    Útil para copiar políticas configuradas en un stream a varios.
    Devuelve el número de streams actualizados.
    """
    src = _supabase().table("rag_sources").select("*").eq("id", source_id).single().execute()
    if not src.data:
        raise ValueError(f"Source {source_id} not found")

    source = src.data
    count = 0
    for stream_id in target_stream_ids:
        new = {k: v for k, v in source.items() if k not in ("id", "created_at")}
        new["stream_id"] = stream_id
        new["scope"] = "stream"
        new["propagated_from"] = source_id
        _supabase().table("rag_sources").insert(new).execute()
        count += 1

    log.info(f"[rag] Propagated source '{source['name']}' to {count} streams")
    return count


# ── Obtener contexto ──────────────────────────────────────────────────────────

def get_always_include_context(org_id: str, stream_id: str | None = None) -> str:
    """
    Devuelve el texto que siempre debe incluirse en el system prompt.
    Combina fuentes globales + fuentes del stream.

    Esto es lo que diferencia a Genie de RAG genérico:
    las políticas y reglas van SIEMPRE en contexto, no se buscan.
    """
    query = (
        _supabase()
        .table("rag_sources")
        .select("name, content, source_type")
        .eq("org_id", org_id)
        .eq("always_include", True)
        .order("source_type")
    )

    # Global + stream específico
    if stream_id:
        query = query.or_(f"scope.eq.global,stream_id.eq.{stream_id}")
    else:
        query = query.eq("scope", "global")

    res = query.execute()
    sources = res.data or []

    if not sources:
        return ""

    lines = ["## Contexto de operación\n"]
    for s in sources:
        lines.append(f"### {s['name']} ({s['source_type']})")
        lines.append(s["content"])
        lines.append("")

    return "\n".join(lines)


def search_context(
    org_id: str,
    query: str,
    stream_id: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Búsqueda semántica en las fuentes del stream y globales.
    Usa pgvector en Supabase.

    TODO: generar embedding del query y usar similarity search.
    Por ahora usa búsqueda de texto (full-text search de Postgres).
    """
    rpc_params = {
        "org_id_input": org_id,
        "query_text": query,
        "stream_id_input": stream_id,
        "match_limit": limit,
    }

    try:
        res = _supabase().rpc("search_rag_sources", rpc_params).execute()
        return res.data or []
    except Exception as e:
        log.warning(f"[rag] Vector search failed, falling back to text: {e}")
        # Fallback a búsqueda de texto simple
        q = (
            _supabase()
            .table("rag_sources")
            .select("name, content, source_type")
            .eq("org_id", org_id)
            .eq("always_include", False)
            .ilike("content", f"%{query}%")
            .limit(limit)
        )
        if stream_id:
            q = q.or_(f"scope.eq.global,stream_id.eq.{stream_id}")
        res = q.execute()
        return res.data or []


def get_full_context(
    org_id: str,
    query: str,
    stream_id: str | None = None,
) -> str:
    """
    Combina contexto fijo (always_include) + resultados de búsqueda semántica.
    Listo para inyectar en el system prompt del agente.
    """
    fixed = get_always_include_context(org_id, stream_id)
    results = search_context(org_id, query, stream_id)

    if not results:
        return fixed

    search_lines = ["\n## Fuentes relevantes\n"]
    for r in results:
        search_lines.append(f"### {r['name']}")
        search_lines.append(r["content"][:1000])  # limitar largo
        search_lines.append("")

    return fixed + "\n".join(search_lines)
