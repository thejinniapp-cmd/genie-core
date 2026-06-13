"""
api/main.py
===========
API principal de Genie. Llamada desde el Workstation (frontend).
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import streams, agents, connectors, jobs, rag, skills, users, dashboard, onboarding

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("genie.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Genie API starting...")
    yield
    log.info("Genie API shutting down.")


app = FastAPI(
    title="Genie API",
    description="Backend de la plataforma Genie",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
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


@app.get("/health")
def health():
    return {"status": "ok", "service": "genie-api"}
