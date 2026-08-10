"""
api/routes/notebooklm_routes.py
================================
Endpoints para el conector NotebookLM.
El flujo es: frontend llama /login → backend abre Playwright → usuario hace login
→ session guardada en Supabase → frontend recarga estado del conector.
"""
import os
import json
import logging
import asyncio
import tempfile
import threading
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from supabase import create_client

from api.auth import get_current_org

log = logging.getLogger("genie.notebooklm_routes")
router = APIRouter()

_login_sessions: dict = {}  # org_id -> {"status": "pending"|"done"|"error", "error": str}


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


@router.post("/login")
def start_notebooklm_login(
    background_tasks: BackgroundTasks,
    org_id: str = Depends(get_current_org),
):
    """
    Inicia el flujo de login de NotebookLM.
    Abre un navegador Chromium (Playwright) para que el usuario haga login con Google.
    """
    if org_id in _login_sessions and _login_sessions[org_id].get("status") == "pending":
        return {"status": "pending", "message": "Login ya está en progreso. Revisa la ventana del navegador."}

    _login_sessions[org_id] = {"status": "pending"}
    background_tasks.add_task(_run_playwright_login, org_id)
    return {"status": "pending", "message": "Abriendo navegador para login con Google en NotebookLM..."}


@router.get("/login-status")
def check_login_status(org_id: str = Depends(get_current_org)):
    """El frontend hace polling aquí para saber si el login terminó."""
    session = _login_sessions.get(org_id, {})
    status = session.get("status", "idle")

    if status == "done":
        del _login_sessions[org_id]
        return {"status": "connected"}
    if status == "error":
        err = session.get("error", "Error desconocido")
        del _login_sessions[org_id]
        raise HTTPException(status_code=500, detail=err)
    return {"status": status}


@router.delete("/disconnect")
def disconnect_notebooklm(org_id: str = Depends(get_current_org)):
    """Elimina la sesión de NotebookLM de la org."""
    _db().table("connectors") \
        .update({"status": "disconnected", "credentials": {}}) \
        .eq("org_id", org_id).eq("connector_type", "notebooklm").execute()
    return {"ok": True}


@router.get("/status")
def notebooklm_status(org_id: str = Depends(get_current_org)):
    """Verifica si NotebookLM está conectado para esta org."""
    res = _db().table("connectors").select("status,credentials") \
        .eq("org_id", org_id).eq("connector_type", "notebooklm").execute()
    if not res.data or res.data[0].get("status") != "connected":
        return {"connected": False}
    return {"connected": True}


def _run_playwright_login(org_id: str):
    """
    Corre en background thread. Abre Chromium con Playwright, espera a que el usuario
    haga login en NotebookLM, captura el storage_state y lo guarda en Supabase.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                slow_mo=100,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            )
            # Ocultar webdriver flag que Google detecta
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()

            # Ir a NotebookLM — Google redirecciona a login si no está autenticado
            page.goto("https://notebooklm.google.com", timeout=15000)

            log.info(f"[notebooklm] Browser opened for org {org_id}. Waiting for login...")

            # Esperar hasta que el usuario llegue a NotebookLM (URL cambia de accounts.google.com)
            try:
                page.wait_for_url("https://notebooklm.google.com/**", timeout=180000)
            except Exception:
                # Intentar también si ya está en la URL final
                pass

            # Esperar 3 segundos extra para que carguen las cookies de sesión
            page.wait_for_timeout(3000)

            # Guardar el storage_state (cookies + localStorage)
            tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            context.storage_state(path=tmp.name)
            tmp.close()

            with open(tmp.name, "r") as f:
                storage_state = json.load(f)

            browser.close()

        # Guardar en Supabase
        from core.connectors.notebooklm import save_storage_state
        save_storage_state(org_id, storage_state)
        _login_sessions[org_id] = {"status": "done"}
        log.info(f"[notebooklm] Session saved for org {org_id}")

    except Exception as e:
        log.error(f"[notebooklm] Login failed for org {org_id}: {e}")
        _login_sessions[org_id] = {"status": "error", "error": str(e)}
