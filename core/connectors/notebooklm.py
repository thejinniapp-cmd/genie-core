"""
core/connectors/notebooklm.py
=============================
Conector NotebookLM para Genie.
Usa notebooklm-py con storage_state guardado en Supabase por org_id.
"""
import os
import json
import asyncio
import logging
import tempfile
from pathlib import Path
from supabase import create_client

log = logging.getLogger("genie.connectors.notebooklm")


def _get_db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def get_storage_state(org_id: str) -> dict | None:
    db = _get_db()
    res = db.table("connectors").select("credentials,status") \
        .eq("org_id", org_id).eq("connector_type", "notebooklm").execute()
    if not res.data or res.data[0].get("status") != "connected":
        return None
    return res.data[0].get("credentials", {}).get("storage_state")


def save_storage_state(org_id: str, storage_state: dict):
    db = _get_db()
    existing = db.table("connectors").select("id") \
        .eq("org_id", org_id).eq("connector_type", "notebooklm").execute()
    if existing.data:
        db.table("connectors").update({
            "status": "connected",
            "credentials": {"storage_state": storage_state},
        }).eq("org_id", org_id).eq("connector_type", "notebooklm").execute()
    else:
        db.table("connectors").insert({
            "org_id": org_id, "connector_type": "notebooklm",
            "status": "connected", "credentials": {"storage_state": storage_state},
        }).execute()
    log.info(f"[notebooklm] Session saved for org {org_id}")


def _get_storage_path(org_id: str) -> Path:
    storage_state = get_storage_state(org_id)
    if not storage_state:
        raise ValueError("NotebookLM no está conectado. Ve a Conectores y haz login.")
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(storage_state, tmp)
    tmp.close()
    return Path(tmp.name)


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result(timeout=120)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


async def _get_auth(org_id: str):
    from notebooklm.auth import AuthTokens
    return await AuthTokens.from_storage(path=_get_storage_path(org_id))


# ── Acciones ──────────────────────────────────────────────────────────────────

def listar_notebooks(org_id: str) -> list:
    from notebooklm import NotebookLMClient
    async def _inner():
        auth = await _get_auth(org_id)
        async with NotebookLMClient(auth=auth) as client:
            notebooks = await client.notebooks.list()
            return [{"id": n.id, "title": n.title} for n in notebooks]
    return _run(_inner())


def crear_notebook(org_id: str, titulo: str, fuentes: list = None) -> dict:
    from notebooklm import NotebookLMClient
    async def _inner():
        auth = await _get_auth(org_id)
        async with NotebookLMClient(auth=auth) as client:
            nb = await client.notebooks.create(title=titulo)
            log.info(f"[notebooklm] Notebook created: id={nb.id!r} title={nb.title!r}")
            if not nb.id:
                raise ValueError(f"Notebook creado pero sin ID válido. Verifica la sesión de NotebookLM.")
            if fuentes:
                for f in fuentes:
                    try:
                        if f.startswith("http"):
                            await client.sources.add_url(nb.id, f)
                        else:
                            await client.sources.add_text(nb.id, f)
                        log.info(f"[notebooklm] Source added to {nb.id}: {f[:60]}")
                    except Exception as e:
                        log.warning(f"[notebooklm] Error adding source {f}: {e}")
            return {"id": nb.id, "title": nb.title,
                    "url": f"https://notebooklm.google.com/notebook/{nb.id}"}
    return _run(_inner())


def agregar_fuente(org_id: str, notebook_id: str, fuente: str) -> dict:
    from notebooklm import NotebookLMClient
    async def _inner():
        auth = await _get_auth(org_id)
        async with NotebookLMClient(auth=auth) as client:
            if fuente.startswith("http"):
                await client.sources.add_url(notebook_id, fuente)
            else:
                await client.sources.add_text(notebook_id, fuente)
            return {"ok": True, "notebook_id": notebook_id, "fuente": fuente}
    return _run(_inner())


def chatear(org_id: str, notebook_id: str, pregunta: str) -> dict:
    from notebooklm import NotebookLMClient
    async def _inner():
        auth = await _get_auth(org_id)
        async with NotebookLMClient(auth=auth) as client:
            response = await client.chat.ask(notebook_id, pregunta)
            return {"respuesta": str(response), "notebook_id": notebook_id}
    return _run(_inner())


def _write_to_stream(org_id: str, stream_id: str, text: str):
    """Escribe un mensaje de assistant al stream (notificación asíncrona)."""
    try:
        db = _get_db()
        db.table("messages").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "role": "assistant",
            "content": {"type": "text", "text": text},
            "metadata": {"agent_id": "notebooklm_background"},
        }).execute()
        log.info(f"[notebooklm] Background message written to stream {stream_id}")
    except Exception as e:
        log.error(f"[notebooklm] Failed to write to stream: {e}")


def generar_contenido(org_id: str, notebook_id: str, tipo: str, instrucciones: str = "", stream_id: str = None) -> dict:
    from notebooklm import NotebookLMClient
    tipo_map = {
        "audio": "audio", "podcast": "audio",
        "infographic": "infographic", "infografia": "infographic", "infografía": "infographic",
        "slides": "slide_deck", "presentacion": "slide_deck", "presentación": "slide_deck",
        "summary": "summary", "resumen": "summary",
        "quiz": "quiz",
    }
    tipo_norm = tipo_map.get(tipo.lower(), tipo.lower())

    tipo_labels = {
        "audio": "podcast", "infographic": "infografía",
        "slide_deck": "presentación", "summary": "resumen", "quiz": "quiz",
    }
    tipo_label = tipo_labels.get(tipo_norm, tipo_norm)
    notebook_url = f"https://notebooklm.google.com/notebook/{notebook_id}"

    log.info(f"[notebooklm] generar_contenido called: notebook={notebook_id} tipo={tipo_norm} stream={stream_id}")

    def _background_generate():
        """Corre en thread separado: espera que las fuentes procesen, genera y notifica."""
        import time

        async def _inner():
            auth = await _get_auth(org_id)
            async with NotebookLMClient(auth=auth) as client:
                # Esperar a que las fuentes terminen de indexarse (máx 3 min, poll cada 10s)
                log.info(f"[notebooklm] Waiting for sources to be indexed in {notebook_id}...")
                for attempt in range(18):
                    source_ids = await client.notebooks.get_source_ids(notebook_id)
                    log.info(f"[notebooklm] Source check {attempt+1}: {len(source_ids)} sources found")
                    if source_ids:
                        break
                    await asyncio.sleep(10)
                else:
                    raise ValueError("Las fuentes no terminaron de indexarse después de 3 minutos.")

                log.info(f"[notebooklm] Starting {tipo_norm} generation for {notebook_id}")
                if tipo_norm == "audio":
                    status = await client.artifacts.generate_audio(notebook_id)
                elif tipo_norm == "infographic":
                    status = await client.artifacts.generate_infographic(notebook_id)
                elif tipo_norm == "slide_deck":
                    status = await client.artifacts.generate_slide_deck(notebook_id)
                else:
                    status = await client.artifacts.generate_study_guide(notebook_id)

                log.info(f"[notebooklm] Generation started task_id={status.task_id}")
                final = await client.artifacts.wait_for_completion(notebook_id, status.task_id, timeout=300)
                log.info(f"[notebooklm] Generation complete url={final.url}")
                return final

        try:
            final = asyncio.run(_inner())
            artifact_url = final.url if (final and final.url) else notebook_url
            if stream_id:
                msg = (
                    f"```genie-widget\n"
                    f'{{"type":"action_result","title":"Tu {tipo_label} está lista ✓",'
                    f'"subtitle":"La generación en NotebookLM terminó.",'
                    f'"data":{{"steps":["✓ {tipo_label.capitalize()} generada exitosamente"],'
                    f'"link":"{artifact_url}","link_label":"Ver {tipo_label}"}}}}\n'
                    f"```"
                )
                _write_to_stream(org_id, stream_id, msg)
        except Exception as e:
            log.error(f"[notebooklm] Background generation failed: {e}", exc_info=True)
            if stream_id:
                _write_to_stream(org_id, stream_id,
                    f"Hubo un error generando la {tipo_label} en NotebookLM: {e}")

    import threading
    thread = threading.Thread(target=_background_generate, daemon=True, name=f"nlm-gen-{notebook_id[:8]}")
    thread.start()
    log.info(f"[notebooklm] Background thread started: {thread.name}, alive={thread.is_alive()}")

    return {
        "tipo": tipo_norm,
        "notebook_id": notebook_id,
        "resultado": f"Generación de {tipo_label} iniciada en background. Te aviso aquí mismo cuando esté lista.",
        "url": notebook_url,
    }
