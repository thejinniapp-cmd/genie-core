"""
api/main.py
===========
API principal de Genie. Llamada desde el Workstation (frontend).
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()  # Carga .env siempre, independiente de cómo se inicia uvicorn
from fastapi.middleware.cors import CORSMiddleware

from api.routes import streams, agents, connectors, jobs, rag, skills, users, dashboard, onboarding, google_oauth, notifications, audit, adn, workflows, notebooklm_routes, marketplace, crm, collections, accounting, inventory, ai_staff

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("genie.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Genie API starting...")
    # Arrancar el watcher service (detecta emails nuevos, cambios en Drive, etc.)
    try:
        from core.watchers.watcher_service import start_watcher_service, stop_watcher_service
        start_watcher_service()
    except Exception as e:
        log.warning(f"Watcher service failed to start: {e}")

    # Arrancar el scheduler del Chief of Staff (tick periódico autónomo)
    try:
        from core.workflows.scheduler_runner import start_scheduler, stop_scheduler
        start_scheduler()
    except Exception as e:
        log.warning(f"Chief of Staff scheduler failed to start: {e}")

    yield

    log.info("Genie API shutting down.")
    try:
        stop_watcher_service()
    except Exception:
        pass
    try:
        stop_scheduler()
    except Exception:
        pass


app = FastAPI(
    title="Genie API",
    description="Backend de la plataforma Genie",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — fail closed: default solo localhost, nunca "*" con credentials
_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
_allowed_origins = [o.strip() for o in _allowed_origins if o.strip() and o.strip() != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rutas
app.include_router(streams.router,    prefix="/api/streams",    tags=["Streams"])
app.include_router(agents.router,     prefix="/api/agents",     tags=["Agents"])
app.include_router(connectors.router, prefix="/api/connectors", tags=["Connectors"])
app.include_router(jobs.router,       prefix="/api/jobs",       tags=["Jobs"])
app.include_router(rag.router,        prefix="/api/rag",        tags=["RAG"])
app.include_router(skills.router,     prefix="/api/skills",     tags=["Skills"])
app.include_router(users.router,      prefix="/api/users",      tags=["Users"])
app.include_router(dashboard.router,  prefix="/api/dashboard",  tags=["Dashboard"])
app.include_router(onboarding.router, prefix="/api/onboarding", tags=["Onboarding"])
app.include_router(google_oauth.router,   prefix="/api/connectors/google", tags=["Google OAuth"])
app.include_router(notifications.router,  prefix="/api/notifications",     tags=["Notifications"])
app.include_router(audit.router,          prefix="/api/audit",              tags=["Audit"])
app.include_router(adn.router,            tags=["ADN/VMS"])
app.include_router(workflows.router,      prefix="/api/workflows",  tags=["Workflows"])
app.include_router(notebooklm_routes.router, prefix="/api/connectors/notebooklm", tags=["NotebookLM"])
app.include_router(marketplace.router,      prefix="/api/marketplace", tags=["Marketplace"])
app.include_router(crm.router,             prefix="/api/crm",      tags=["CRM"])
app.include_router(collections.router,     prefix="/api/collections", tags=["Collections"])
app.include_router(accounting.router,     prefix="/api/accounting", tags=["Accounting"])
app.include_router(inventory.router,       prefix="/api/inventory", tags=["Inventory"])
app.include_router(ai_staff.router,        prefix="/api/ai-staff", tags=["AI Staff"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "genie-api"}
