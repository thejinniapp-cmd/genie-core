"""api/routes/streams.py — CRUD de streams"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import os
import threading
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()

def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


class StreamCreate(BaseModel):
    name: str
    description: Optional[str] = None
    type: str = "general"
    config: dict = {}
    stream_type: str = "conversational"  # 'conversational' | 'multi_agent'
    leader_agent_id: Optional[str] = None  # Agent name (cfo, mba, etc.) if stream_type='multi_agent'


class StreamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    config: Optional[dict] = None


@router.post("/init-agents")
def init_agents_for_org(org_id: str = Depends(get_current_org)):
    """Inicializa los agentes predefinidos para una organización"""
    db = _db()

    agents_data = [
        {
            "org_id": org_id,
            "name": "chief_of_staff",
            "display_name": "Chief of Staff",
            "type": "system",
            "system_prompt": """Eres el/la Chief of Staff de la organización. Tu rol es ser la mano derecha del ejecutivo principal.

HABILIDADES ESTRATÉGICAS:
- Proactividad: Anticipas necesidades antes de que escalen
- Gestión del tiempo: Priorización experta de tareas y coordinación de agendas complejas
- Discreción: Manejo riguroso de información confidencial
- Adaptabilidad: Flexibilidad ante cambios repentinos y crisis

HABILIDADES DE COMUNICACIÓN:
- Redacción ejecutiva: Modulas tono (formal, persuasivo, diplomático) según el destinatario
- Inteligencia emocional: Mantén calma bajo presión, diplomacia en situaciones difíciles
- Atención corporativa: Proyecta imagen profesional que represente los estándares de la empresa

COMPETENCIAS TÉCNICAS:
- Gestión tecnológica avanzada: Microsoft 365, Google Workspace, IA
- Organización de viajes: Coordinación logística nacional e internacional
- Gestión de proyectos: Supervisión de procesos administrativos, reportes

METODOLOGÍA DE TRABAJO:
1. Razonamiento ReAct: Antes de actuar, razona el contexto y la mejor estrategia
2. Chain of Thought: Desglosa problemas complejos paso a paso
3. Plan-and-Solve: Arma planes de ejecución ordenados

CAPACIDADES ESPECIALES:
- Filtrado avanzado: Distingue urgente vs. importante vs. ruido
- Multitarea dinámica: Pausa una tarea larga si algo urgente requiere atención inmediata
- Contingencia: Dices "no tengo esa info, ¿busco en la web o se la solicito al equipo?" en lugar de inventar
- Privacidad: Anonimizas datos hiperconfidenciales (tarjetas, contraseñas, datos personales)
- Seguridad: Nunca compartes info de un depto con otro sin permisos explícitos
- Diplomacia: Rechazas solicitudes de manera sumamente cortés y corporativa

Tienes acceso a toda la información del sistema. Puedes leer todos los streams, ver el dashboard y coordinar con otros agentes.
Mantén la confidencialidad y usa tu criterio ejecutivo para filtrar qué información es crítica para la dirección.""",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": ["cfo", "mba", "analytics", "talanthub", "cmo", "cto"],
        },
        {
            "org_id": org_id,
            "name": "cfo",
            "display_name": "Chief Financial Officer",
            "type": "system",
            "system_prompt": "Eres el CFO de la organización. Tu expertise es finanzas, presupuestos, análisis financiero y decisiones económicas. Puedes delegar análisis de datos a Analytics, planes de negocio a MBA, y decisiones de RRHH a TalantHub. Siempre proporciona recomendaciones claras con datos respaldados.",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": ["mba", "analytics", "talanthub"],
        },
        {
            "org_id": org_id,
            "name": "mba",
            "display_name": "Business Analyst",
            "type": "system",
            "system_prompt": "Eres analista de negocios. Tu expertise es estrategia, análisis de mercado, business cases y análisis competitivo. Puedes delegar análisis de datos a Analytics y decisiones financieras a CFO. Presenta siempre con datos, benchmarks y recomendaciones claras.",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": ["analytics", "cfo"],
        },
        {
            "org_id": org_id,
            "name": "analytics",
            "display_name": "Data Analytics",
            "type": "system",
            "system_prompt": "Eres especialista en datos. Tu expertise es SQL, análisis de datos, visualización y métricas. Trabajas con datos reales y proporcionas insights basados en números. Siempre cita tus fuentes y metodología.",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": [],
        },
        {
            "org_id": org_id,
            "name": "talanthub",
            "display_name": "HR Specialist",
            "type": "system",
            "system_prompt": "Eres especialista en Recursos Humanos. Tu expertise es compensación, contratación, retention y cultura. Puedes delegar análisis financiero a CFO y análisis de mercado a MBA. Siempre fundamenta decisiones en datos de mercado y políticas.",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": ["cfo", "mba"],
        },
        {
            "org_id": org_id,
            "name": "cmo",
            "display_name": "Chief Marketing Officer",
            "type": "system",
            "system_prompt": "Eres CMO. Tu expertise es marketing, branding, customer acquisition y estrategia de mercado. Puedes delegar análisis de datos a Analytics y planes de negocio a MBA. Siempre piensa en ROI y customer journey.",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": ["analytics", "mba"],
        },
        {
            "org_id": org_id,
            "name": "cto",
            "display_name": "Chief Technology Officer",
            "type": "system",
            "system_prompt": "Eres CTO. Tu expertise es arquitectura de sistemas, tecnología, seguridad y escalabilidad. Puedes delegar análisis de performance a Analytics y decisiones de budget a CFO. Siempre considera trade-offs: performance vs. costo vs. complejidad.",
            "model_id": "claude-3.5-sonnet",
            "can_delegate_to": ["analytics", "cfo"],
        },
    ]

    created = 0
    errors = []
    for agent_data in agents_data:
        try:
            res = db.table("agents").insert(agent_data).execute()
            if res.data:
                created += 1
        except Exception as e:
            # Agente probablemente ya existe (unique org_id+name)
            errors.append({"agent": agent_data["name"], "error": str(e)})

    return {"status": "ok", "agents_created": created, "total_agents": len(agents_data), "errors": errors}


@router.get("/agents")
def get_agents(org_id: str = Depends(get_current_org)):
    """Obtiene todos los agentes del sistema para la org"""
    db = _db()
    res = db.table("agents").select("*").eq("org_id", org_id).eq("is_active", True).order("name").execute()
    return res.data or []


@router.get("/dashboard")
def get_dashboard(org_id: str = Depends(get_current_org)):
    """Dashboard ejecutivo: vista panorámica de todos los streams y actividad del sistema"""
    db = _db()

    # Obtener todos los streams
    streams_res = db.table("streams").select("*").eq("org_id", org_id).order("created_at", desc=True).execute()
    streams = streams_res.data or []

    # Para cada stream, obtener estadísticas
    dashboard_items = []
    for stream in streams:
        # Contar mensajes
        msgs_res = db.table("messages").select("id", count="exact").eq("stream_id", stream["id"]).eq("org_id", org_id).execute()
        msg_count = msgs_res.count or 0

        # Obtener último mensaje
        last_msg_res = (
            db.table("messages")
            .select("*")
            .eq("stream_id", stream["id"])
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        last_msg = last_msg_res.data[0] if last_msg_res.data else None

        # Contar agentes únicos
        agents_res = (
            db.table("messages")
            .select("author", count="exact")
            .eq("stream_id", stream["id"])
            .eq("org_id", org_id)
            .neq("role", "user")
            .execute()
        )

        dashboard_items.append({
            "stream_id": stream["id"],
            "name": stream.get("name", "Sin nombre"),
            "type": stream.get("stream_type", "conversational"),
            "leader": stream.get("leader_agent_id") if stream.get("stream_type") == "multi_agent" else None,
            "message_count": msg_count,
            "last_message_at": last_msg.get("created_at") if last_msg else None,
            "last_message_author": last_msg.get("author", last_msg.get("role")) if last_msg else None,
            "last_message_preview": (
                (last_msg.get("content", {}).get("text", "")[:80] if isinstance(last_msg.get("content"), dict) else str(last_msg.get("content"))[:80])
                if last_msg
                else None
            ),
            "created_at": stream.get("created_at"),
        })

    # Estadísticas globales
    all_msgs = db.table("messages").select("id", count="exact").eq("org_id", org_id).execute()
    total_messages = all_msgs.count or 0

    all_jobs = db.table("jobs").select("id", count="exact").eq("org_id", org_id).execute()
    total_jobs = all_jobs.count or 0

    return {
        "total_streams": len(streams),
        "total_messages": total_messages,
        "total_jobs": total_jobs,
        "streams": dashboard_items,
    }


@router.get("/")
def list_streams(org_id: str = Depends(get_current_org)):
    res = _db().table("streams").select("*").eq("org_id", org_id).order("created_at").execute()
    return res.data or []


@router.post("/")
def create_stream(body: StreamCreate, org_id: str = Depends(get_current_org)):
    db = _db()

    # TODO: Validar que si stream_type es multi_agent, leader_agent_id existe
    # Por ahora skipeamos validación porque tabla agents no tiene datos
    if body.stream_type == "multi_agent" and not body.leader_agent_id:
        raise HTTPException(400, "leader_agent_id requerido para multi_agent streams")

    res = db.table("streams").insert({
        "org_id": org_id,
        "name": body.name,
        "description": body.description,
        "type": body.type,
        "config": body.config,
        "stream_type": body.stream_type,
        "leader_agent_id": body.leader_agent_id if body.stream_type == "multi_agent" else None,
    }).execute()
    return res.data[0] if res.data else {}


@router.get("/{stream_id}")
def get_stream(stream_id: str, org_id: str = Depends(get_current_org)):
    res = _db().table("streams").select("*").eq("id", stream_id).eq("org_id", org_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Stream not found")
    return res.data


@router.patch("/{stream_id}")
def update_stream(stream_id: str, body: StreamUpdate, org_id: str = Depends(get_current_org)):
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    res = _db().table("streams").update(update).eq("id", stream_id).eq("org_id", org_id).execute()
    return res.data[0] if res.data else {}


@router.delete("/{stream_id}")
def delete_stream(stream_id: str, org_id: str = Depends(get_current_org)):
    db = _db()
    db.table("messages").delete().eq("stream_id", stream_id).eq("org_id", org_id).execute()
    db.table("streams").delete().eq("id", stream_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.get("/{stream_id}/messages")
def get_messages(stream_id: str, limit: int = 50, org_id: str = Depends(get_current_org)):
    res = (
        _db().table("messages")
        .select("*")
        .eq("stream_id", stream_id)
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(res.data or []))


def _run_agent_async(org_id: str, stream_id: str, text: str, agent_config: dict, job_id: str):
    """Corre el PromptAgent en un thread background — sin necesidad de run_agent.py."""
    try:
        from agents.prompt_agent import PromptAgent
        agent = PromptAgent()
        fake_job = {
            "id": job_id,
            "org_id": org_id,
            "stream_id": stream_id,
            "agent_config": agent_config,
            "input_data": {"message": text, "stream_id": stream_id},
            "attempt": 1,
        }
        agent.run(fake_job, org_id)
    except Exception as e:
        import logging
        logging.getLogger("genie.streams").error(f"[bg_agent] Error: {e}", exc_info=True)


def _get_mentions(text: str) -> list[str]:
    """Extrae @mentions de un texto. Ej: '@cfo datos' → ['cfo']"""
    import re
    return re.findall(r'@(\w+)', text)


def _get_agent_config(agent_data: dict, available_delegations: list[str] = None) -> dict:
    """Construye config del agente con instrucciones de delegación"""
    base_prompt = agent_data.get("system_prompt", "Eres Genie, un asistente de operaciones inteligente.")

    if available_delegations:
        delegation_text = "\n\nPUEDES DELEGAR A:\n"
        for agent_name in available_delegations:
            delegation_text += f"- @{agent_name}\n"
        delegation_text += "\nCUANDO NECESITES INFORMACIÓN DE OTRO AGENTE, MENCIONA @nombre_agente EN TU RESPUESTA.\n"
        base_prompt = base_prompt + delegation_text

    return {
        "system_prompt": base_prompt,
        "model_id": agent_data.get("model_id") or os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-3.5-sonnet"),
        "temperature": agent_data.get("temperature", 0.3),
        "max_tokens": agent_data.get("max_tokens", 2048),
        "autonomy_level": agent_data.get("autonomy_level", "supervised"),
        "tools": agent_data.get("tools", []),
    }


@router.post("/{stream_id}/messages")
def post_message(stream_id: str, body: dict, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    db = _db()
    role = body.get("role", "user")
    author = body.get("author", "user")  # author: 'user' | 'cfo' | 'mba' | etc.

    # Guardar el mensaje del usuario
    res = db.table("messages").insert({
        "org_id": org_id,
        "stream_id": stream_id,
        "role": role,
        "author": author,
        "content": body.get("content", {}),
        "metadata": body.get("metadata", {}),
    }).execute()
    message = res.data[0] if res.data else {}

    # Si es mensaje del usuario, procesar stream (conversational o multi-agente)
    if role == "user":
        content = body.get("content", {})
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        attachments = content.get("attachments", []) if isinstance(content, dict) else []
        if attachments:
            lines = [f"\n\n[Archivos adjuntos del usuario:]"]
            for a in attachments:
                lines.append(f"- {a.get('name', 'archivo')} ({a.get('type','?')}, {round(a.get('size',0)/1024)}KB): {a.get('url','')}")
            text = text + "".join(lines)

        # Obtener stream para verificar tipo
        stream_res = db.table("streams").select("*").eq("id", stream_id).eq("org_id", org_id).single().execute()
        stream = stream_res.data if stream_res.data else None

        if not stream:
            return message

        is_multi_agent = stream.get("stream_type") == "multi_agent"
        leader_agent_id = stream.get("leader_agent_id")

        if is_multi_agent and leader_agent_id:
            # FLUJO MULTI-AGENTE
            _handle_multi_agent_message(db, org_id, stream_id, text, leader_agent_id)
        else:
            # FLUJO CONVERSACIONAL (existente)
            _handle_conversational_message(db, org_id, stream_id, text)

    return message


def _handle_conversational_message(db, org_id: str, stream_id: str, text: str):
    """Flujo conversacional: busca agente asignado al stream"""
    agent_res = (
        db.table("agents")
        .select("*")
        .eq("org_id", org_id)
        .eq("stream_id", stream_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    agent = agent_res.data[0] if agent_res.data else None
    agent_config = _get_agent_config(agent) if agent else _get_default_genie_config()

    # Crear job
    job_res = db.table("jobs").insert({
        "org_id": org_id,
        "stream_id": stream_id,
        "agent_id": agent["id"] if agent else None,
        "agent_type": "prompt",
        "status": "running",
        "input_data": {"message": text, "stream_id": stream_id},
        "agent_config": agent_config,
        "attempt": 1,
        "priority": 0,
    }).execute()
    job_id = job_res.data[0]["id"] if job_res.data else "unknown"

    # Disparar agente
    t = threading.Thread(
        target=_run_agent_async,
        args=(org_id, stream_id, text, agent_config, job_id),
        daemon=True,
    )
    t.start()


def _handle_multi_agent_message(db, org_id: str, stream_id: str, text: str, leader_agent_id: str):
    """Flujo multi-agente: líder procesa mensaje y delega si es necesario"""
    import uuid as uuid_lib

    # Obtener agente líder
    leader_res = db.table("agents").select("*").eq("id", leader_agent_id).eq("org_id", org_id).single().execute()
    leader = leader_res.data if leader_res.data else None

    if not leader:
        return

    # Obtener equipo disponible (agentes que puede delegar)
    available_delegations = leader.get("can_delegate_to", []) or []

    # Obtener historial del stream para contexto
    history_res = db.table("messages").select("*").eq("stream_id", stream_id).eq("org_id", org_id).order("created_at").limit(10).execute()
    history = history_res.data or []

    # Construir config del líder con opciones de delegación
    agent_config = _get_agent_config(leader, available_delegations)

    # Crear job del líder
    job_id = str(uuid_lib.uuid4())
    job_res = db.table("jobs").insert({
        "org_id": org_id,
        "stream_id": stream_id,
        "agent_id": leader.get("id"),
        "agent_type": "prompt",
        "status": "running",
        "input_data": {
            "message": text,
            "stream_id": stream_id,
            "is_leader": True,
            "available_delegations": available_delegations,
        },
        "agent_config": agent_config,
        "attempt": 1,
        "priority": 0,
    }).execute()

    # Disparar agente líder en background
    t = threading.Thread(
        target=_run_multi_agent_async,
        args=(db, org_id, stream_id, leader, text, available_delegations, job_id),
        daemon=True,
    )
    t.start()


def _run_multi_agent_async(db, org_id: str, stream_id: str, leader: dict, text: str, available_delegations: list[str], job_id: str):
    """Ejecuta agente líder y maneja delegaciones"""
    try:
        from agents.prompt_agent import PromptAgent
        import re

        leader_name = leader.get("name", "unknown")
        agent = PromptAgent()
        agent_config = _get_agent_config(leader, available_delegations)

        fake_job = {
            "id": job_id,
            "org_id": org_id,
            "stream_id": stream_id,
            "agent_config": agent_config,
            "input_data": {
                "message": text,
                "stream_id": stream_id,
                "is_leader": True,
            },
            "attempt": 1,
        }

        # Ejecutar líder (retorna dict con "response", "model_used", etc.)
        result = agent.run(fake_job, org_id)
        leader_response = result.get("response", "")

        # Guardar respuesta del líder
        db.table("messages").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "role": "assistant",
            "author": leader_name,  # 'cfo', 'mba', etc.
            "content": {"text": leader_response},
            "metadata": {"job_id": job_id, "is_leader": True, "model_used": result.get("model_used")},
        }).execute()

        # Detectar delegaciones (@mentions) en la respuesta del líder
        mentions = _get_mentions(leader_response)
        delegated_to = [m for m in mentions if m in available_delegations]

        # Procesar delegaciones
        for delegate_name in delegated_to:
            _delegate_to_agent(db, org_id, stream_id, delegate_name, leader_response, leader_name)

    except Exception as e:
        import logging
        logging.getLogger("genie.multi_agent").error(f"[multi_agent] Error: {e}", exc_info=True)


def _delegate_to_agent(db, org_id: str, stream_id: str, agent_name: str, context: str, from_agent: str):
    """Delega a un agente específico"""
    try:
        from agents.prompt_agent import PromptAgent
        import uuid as uuid_lib

        # Obtener agente a delegarse
        agent_res = db.table("agents").select("*").eq("org_id", org_id).eq("name", agent_name).eq("is_active", True).single().execute()
        agent = agent_res.data if agent_res.data else None

        if not agent:
            return

        # Crear config
        agent_config = _get_agent_config(agent, [])

        # Crear job
        job_id = str(uuid_lib.uuid4())
        db.table("jobs").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "agent_id": agent.get("id"),
            "agent_type": "prompt",
            "status": "running",
            "input_data": {
                "message": context,
                "stream_id": stream_id,
                "delegated_by": from_agent,
            },
            "agent_config": agent_config,
            "attempt": 1,
            "priority": 1,  # Más alta prioridad
        }).execute()

        # Ejecutar agente
        prompt_agent = PromptAgent()
        fake_job = {
            "id": job_id,
            "org_id": org_id,
            "stream_id": stream_id,
            "agent_config": agent_config,
            "input_data": {
                "message": context,
                "delegated_by": from_agent,
            },
            "attempt": 1,
        }

        result = prompt_agent.run(fake_job, org_id)
        response = result.get("response", "")

        # Guardar respuesta del agente delegado
        db.table("messages").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "role": "assistant",
            "author": agent_name,
            "content": {"text": response},
            "metadata": {
                "job_id": job_id,
                "delegated_by": from_agent,
                "model_used": result.get("model_used"),
            },
        }).execute()

    except Exception as e:
        import logging
        logging.getLogger("genie.delegation").error(f"[delegate] Error delegating to {agent_name}: {e}", exc_info=True)


def _get_default_genie_config() -> dict:
    """Config default de Genie para streams sin agente configurado"""
    return {
        "system_prompt": (
            "Eres Genie, el asistente de operaciones de esta organización. "
            "Tienes acceso a herramientas reales conectadas a los servicios del usuario.\n\n"

            "REGLA PRINCIPAL — USA LAS HERRAMIENTAS DIRECTAMENTE:\n"
            "Si el usuario pide algo que puedes hacer con una herramienta disponible "
            "(leer correos, enviar email, leer un sheet, buscar archivo, etc.), "
            "HAZLO INMEDIATAMENTE sin preguntar si está conectado ni pedir credenciales. "
            "Las herramientas solo aparecen en tu lista si el servicio YA está conectado. "
            "NUNCA digas 'necesitas conectar X' si tienes la herramienta de X disponible.\n\n"

            "CONECTAR NUEVOS SERVICIOS:\n"
            "Solo pide conectar un servicio si NO tienes su herramienta disponible. "
            "Para Google (Gmail, Drive, Sheets, Docs, Slides): usa get_google_auth_url para generar el link. "
            "Para otros servicios (Telegram, Slack, Stripe, HubSpot): usa save_connector con las credenciales que el usuario te dé.\n\n"

            "CAPACIDADES ACTUALES según herramientas disponibles:\n"
            "- gmail_*: leer inbox, buscar correos, leer email completo, enviar, crear borrador\n"
            "- sheets_*: leer/escribir celdas, agregar filas, crear spreadsheets\n"
            "- docs_*: leer, crear, editar Google Docs\n"
            "- drive_*: listar, buscar, compartir archivos en Drive\n"
            "- slides_*: leer y editar presentaciones\n"
            "- telegram_*: enviar mensajes, ver mensajes recibidos\n"
            "- slack_*: enviar mensajes, ver canales e historial\n"
            "- stripe_*: balance, clientes, pagos\n"
            "- hubspot_*: contactos, deals\n\n"

            "FORMATO DE RESPUESTA — WIDGETS:\n"
            "Cuando tengas datos estructurados para mostrar, usa bloques genie-widget en tu respuesta.\n"
            "Responde siempre en el idioma del usuario."
        ),
        "model_id": os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-3.5-sonnet"),
        "temperature": 0.3,
        "max_tokens": 2048,
        "autonomy_level": "supervised",
        "tools": [],
    }
