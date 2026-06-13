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
│   ├── executor.py     # Loop principal de jobs
│   ├── audit.py        # Audit log inmutable
│   ├── rag/            # RAG global + por stream
│   └── connectors/     # Integraciones MCP/API (gmail, drive, crm...)
├── agents/             # Sistema de agentes
│   ├── base_agent.py   # Clase base — todos los agentes heredan aquí
│   ├── prompt_agent.py # Agente ligero — solo prompt + conectores
│   ├── bot_agent.py    # Agente con canal (WhatsApp, Telegram...)
│   └── workers/        # Workers Python para lógica compleja
├── skills/             # Marketplace de skills
│   ├── official/       # Skills oficiales de Genie
│   └── community/      # Skills subidas por la comunidad
├── channels/           # Canales de comunicación
│   ├── telegram.py
│   ├── whatsapp.py
│   └── email.py
├── portal/             # Portal externo (clientes, proveedores)
│   ├── app.py          # FastAPI app del portal
│   └── chat.py         # Chat bot del portal
├── api/                # API principal (llamada desde el Workstation)
│   ├── main.py         # FastAPI app principal
│   ├── routes/         # Endpoints por dominio
│   └── middleware/     # Auth, logging, rate limiting
├── dashboard/          # Métricas y analytics
│   ├── metrics.py      # KPIs del sistema
│   └── custom_kpis.py  # KPIs definidos por el usuario
├── db/                 # Base de datos
│   └── migrations/     # Migraciones SQL
└── docs/               # Documentación
    └── skill_format.md # Spec del formato .md para skills
```

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python + FastAPI |
| Base de datos | Supabase (PostgreSQL + pgvector) |
| Workers | Railway |
| AI | OpenRouter (Claude, GPT-4o, Gemini, local) |
| Conectores | MCP protocol |
| Canales | WhatsApp Business API, Telegram Bot API |

## Conceptos clave

**Tenant** — cada organización es un tenant. Todas las credenciales, streams, agentes y datos están aislados por `org_id`.

**Stream** — un flujo de trabajo con su propio contexto, agentes asignados, RAG, y memoria. Equivale a un área del negocio (ventas, soporte, operaciones).

**Skill** — proceso de negocio preconfigurado. Puede ser un archivo `.md` (prompt + instrucciones) o un worker `.py` (lógica compleja).

**Audit log** — registro inmutable de cada evento del sistema. Cada entrada incluye hash encadenado para garantizar integridad.

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
