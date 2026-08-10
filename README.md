# Genie Core

> El equipo que tu empresa no puede costear. Por una fracción del precio.

Genie es una plataforma SaaS de agentes de IA que opera como el gerente de operaciones que cualquier empresa necesita. Conecta todas las herramientas del negocio, despliega agentes especializados, y mantiene al dueño informado sin ahogarlo en detalles.

**Principio rector:** 1 person, 1 billion dollar company — sin conocimientos técnicos.

---

## Arquitectura

```
genie-core/
├── core/               # Motor central
│   ├── tenant.py       # Resolución de credenciales por organización
│   ├── audit.py        # Audit log
│   ├── workflow_engine.py  # Motor de workflows (lista + panel, aprobaciones)
│   ├── rag/            # RAG global + por stream
│   ├── watchers/       # Watchers (detecta emails nuevos, etc.)
│   └── connectors/     # Integraciones (gmail, drive, sheets, docs, slides,
│                       #   calendar, slack, telegram, stripe, hubspot, notebooklm)
├── agents/             # Sistema de agentes
│   ├── base_agent.py   # Clase base — todos los agentes heredan aquí
│   ├── prompt_agent.py # Agente ligero — prompt + conectores
│   └── workers/        # Workers Python para lógica compleja
├── skills/             # Skills de negocio
│   └── official/       # Skills oficiales de Genie
├── api/                # API principal (llamada desde el Workstation)
│   ├── main.py         # FastAPI app principal
│   ├── auth.py         # Resolución de identidad/org (JWT Supabase + X-Org-Id dev)
│   └── routes/         # Endpoints por dominio
├── db/                 # Base de datos
│   └── migrations/     # Migraciones SQL iniciales
├── migrations/         # Migraciones incrementales
└── docs/               # Documentación
    └── skill_format.md # Spec del formato .md para skills
```

Roadmap (aún no existe en código): canales (WhatsApp/email), portal externo
(clientes/proveedores), custom KPIs, marketplace de skills de comunidad.

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python + FastAPI |
| Base de datos | Supabase (PostgreSQL + pgvector) |
| Workers | Railway |
| AI | OpenRouter (Claude, GPT-4o, Gemini, local) |
| Conectores | APIs directas (Google, Slack, Stripe...) |
| Canales | Telegram Bot API (WhatsApp en roadmap) |

## Conceptos clave

**Tenant** — cada organización es un tenant. Todas las credenciales, streams, agentes y datos están aislados por `org_id`.

**Stream** — un flujo de trabajo con su propio contexto, agentes asignados, RAG, y memoria. Equivale a un área del negocio (ventas, soporte, operaciones).

**Skill** — proceso de negocio preconfigurado. Puede ser un archivo `.md` (prompt + instrucciones) o un worker `.py` (lógica compleja).

**Audit log** — registro de cada evento del sistema con hash encadenado por entrada (verificación de cadena en `core/audit.py`).

**Autonomía dial** — cada proceso tiene un nivel configurable: `manual` → `supervised` → `autonomous`.

## Inicio rápido

```bash
pip install -r requirements.txt
cp .env.example .env
# Configurar variables de entorno
uvicorn api.main:app --reload
```

## Variables de entorno

Ver `.env.example` para la lista completa.
