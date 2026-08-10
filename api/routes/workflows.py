"""api/routes/workflows.py — Workflow Engine API"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
from supabase import create_client

from api.auth import get_current_org
from core.workflows.module_defaults import seed_module_workflows
from core.workflows.scheduler import tick_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ── Modelos ───────────────────────────────────────────────────────────────────

class StepCreate(BaseModel):
    name: str
    description: Optional[str] = None
    step_order: int
    type: str = "human_internal"
    agent_id: Optional[str] = None
    assigned_to: Optional[str] = None
    condition_expr: Optional[str] = None
    advance_condition: str = "all_approved"
    deadline_offset_days: Optional[int] = None
    alert_before_days: int = 1
    config: dict = {}
    tasks: List[dict] = []


class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str = "manual"
    trigger_config: dict = {}
    has_anchor_date: bool = False
    is_catalog: bool = True
    steps: List[StepCreate] = []


class TemplateUpdate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str = "manual"
    trigger_config: dict = {}
    has_anchor_date: bool = False
    is_catalog: bool = False
    steps: List[StepCreate] = []


class TemplateDuplicate(BaseModel):
    name: Optional[str] = None
    is_catalog: bool = False


class RunStart(BaseModel):
    template_id: str
    input_data: dict = {}
    name: Optional[str] = None
    stream_id: Optional[str] = None
    anchor_date: Optional[str] = None
    created_by: Optional[str] = None


class TaskComplete(BaseModel):
    run_task_id: str
    response_data: dict = {}
    actor: str
    status: str = "approved"
    rejection_reason: Optional[str] = None


class SchedulerTick(BaseModel):
    module_key: Optional[str] = None


class ModuleSeed(BaseModel):
    module_key: str


class NLTemplateCreate(BaseModel):
    description: str
    trigger_type: str = "manual"
    dry_run: bool = False  # true = solo interpretar, no persistir (confirmación 15b)


# ── Templates ─────────────────────────────────────────────────────────────────

@router.get("/templates")
def list_templates(org_id: str = Depends(get_current_org)):
    """Catálogo de plantillas reutilizables (excluye copias privadas creadas al editar)."""
    res = (
        _db().table("workflow_templates").select("*")
        .eq("org_id", org_id).eq("is_active", True).eq("is_catalog", True)
        .order("created_at", desc=True).execute()
    )
    return res.data or []


@router.post("/templates")
def create_template(body: TemplateCreate, org_id: str = Depends(get_current_org)):
    db = _db()

    # Crear template
    tmpl = db.table("workflow_templates").insert({
        "org_id": org_id,
        "name": body.name,
        "description": body.description,
        "trigger_type": body.trigger_type,
        "trigger_config": body.trigger_config,
        "has_anchor_date": body.has_anchor_date,
        "is_catalog": body.is_catalog,
    }).execute().data

    if not tmpl:
        raise HTTPException(500, "Could not create template")
    template_id = tmpl[0]["id"]

    # Crear steps y sus tasks
    for step in body.steps:
        step_row = db.table("workflow_steps").insert({
            "template_id": template_id,
            "name": step.name,
            "description": step.description,
            "step_order": step.step_order,
            "type": step.type,
            "agent_id": step.agent_id,
            "assigned_to": step.assigned_to,
            "condition_expr": step.condition_expr,
            "advance_condition": step.advance_condition,
            "deadline_offset_days": step.deadline_offset_days,
            "alert_before_days": step.alert_before_days,
            "config": step.config,
        }).execute().data

        if not step_row:
            continue
        step_id = step_row[0]["id"]

        for task in step.tasks:
            db.table("workflow_tasks").insert({
                "step_id": step_id,
                "name": task.get("name", "Tarea"),
                "description": task.get("description"),
                "input_type": task.get("input_type", "approval"),
                "assigned_to_role": task.get("assigned_to_role", "reviewer"),
                "reviewer_emails": task.get("reviewer_emails", []),
                "depends_on": task.get("depends_on", []),
                "template_url": task.get("template_url"),
                "versioned": task.get("versioned", False),
                "task_config": task.get("task_config", {}),
            }).execute()

    return db.table("workflow_templates").select("*").eq("id", template_id).single().execute().data


@router.get("/templates/{template_id}")
def get_template(template_id: str, org_id: str = Depends(get_current_org)):
    db = _db()
    tmpl = db.table("workflow_templates").select("*").eq("id", template_id).eq("org_id", org_id).single().execute().data
    if not tmpl:
        raise HTTPException(404, "Template not found")

    steps = db.table("workflow_steps").select("*").eq("template_id", template_id).order("step_order").execute().data or []
    for step in steps:
        step["tasks"] = db.table("workflow_tasks").select("*").eq("step_id", step["id"]).execute().data or []

    tmpl["steps"] = steps
    return tmpl


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, org_id: str = Depends(get_current_org)):
    _db().table("workflow_templates").update({"is_active": False}).eq("id", template_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.put("/templates/{template_id}")
def update_template(template_id: str, body: TemplateUpdate, org_id: str = Depends(get_current_org)):
    """
    Reemplaza nombre/descripción/steps de un template existente.
    Pensado para usarse SOLO sobre copias privadas (creadas con /duplicate), nunca
    sobre la plantilla base del catálogo — así la base nunca se muta in-place.
    """
    db = _db()

    existing = db.table("workflow_templates").select("id").eq("id", template_id).eq("org_id", org_id).single().execute().data
    if not existing:
        raise HTTPException(404, "Template not found")

    db.table("workflow_templates").update({
        "name": body.name,
        "description": body.description,
        "trigger_type": body.trigger_type,
        "trigger_config": body.trigger_config,
        "has_anchor_date": body.has_anchor_date,
        "is_catalog": body.is_catalog,
    }).eq("id", template_id).execute()

    # Borrar steps existentes (cascada borra sus tasks) y recrear con los nuevos
    db.table("workflow_steps").delete().eq("template_id", template_id).execute()

    for step in body.steps:
        step_row = db.table("workflow_steps").insert({
            "template_id": template_id,
            "name": step.name,
            "description": step.description,
            "step_order": step.step_order,
            "type": step.type,
            "agent_id": step.agent_id,
            "assigned_to": step.assigned_to,
            "condition_expr": step.condition_expr,
            "advance_condition": step.advance_condition,
            "deadline_offset_days": step.deadline_offset_days,
            "alert_before_days": step.alert_before_days,
            "config": step.config,
        }).execute().data

        if not step_row:
            continue
        step_id = step_row[0]["id"]

        for task in step.tasks:
            db.table("workflow_tasks").insert({
                "step_id": step_id,
                "name": task.get("name", "Tarea"),
                "description": task.get("description"),
                "input_type": task.get("input_type", "approval"),
                "assigned_to_role": task.get("assigned_to_role", "reviewer"),
                "reviewer_emails": task.get("reviewer_emails", []),
                "depends_on": task.get("depends_on", []),
                "template_url": task.get("template_url"),
                "versioned": task.get("versioned", False),
                "task_config": task.get("task_config", {}),
            }).execute()

    return get_template(template_id, org_id)


@router.post("/templates/{template_id}/duplicate")
def duplicate_template(template_id: str, body: TemplateDuplicate, org_id: str = Depends(get_current_org)):
    """
    Crea una copia independiente del template (steps + tasks incluidos).
    La plantilla original NO se modifica — sigue disponible en el catálogo para otros casos de uso.
    Por defecto la copia es privada (is_catalog=False): solo sirve para correr un proceso puntual,
    a menos que se pida explícitamente que también quede en el catálogo.
    """
    db = _db()
    source = get_template(template_id, org_id)  # 404 si no existe / no es de esta org

    new_tmpl = db.table("workflow_templates").insert({
        "org_id": org_id,
        "name": body.name or source["name"],
        "description": source.get("description"),
        "trigger_type": source.get("trigger_type", "manual"),
        "trigger_config": source.get("trigger_config", {}),
        "has_anchor_date": source.get("has_anchor_date", False),
        "is_catalog": body.is_catalog,
    }).execute().data

    if not new_tmpl:
        raise HTTPException(500, "Could not duplicate template")
    new_id = new_tmpl[0]["id"]

    for step in source.get("steps", []):
        step_row = db.table("workflow_steps").insert({
            "template_id": new_id,
            "name": step["name"],
            "description": step.get("description"),
            "step_order": step["step_order"],
            "type": step["type"],
            "agent_id": step.get("agent_id"),
            "assigned_to": step.get("assigned_to"),
            "condition_expr": step.get("condition_expr"),
            "advance_condition": step.get("advance_condition", "all_approved"),
            "deadline_offset_days": step.get("deadline_offset_days"),
            "alert_before_days": step.get("alert_before_days", 1),
            "config": step.get("config", {}),
        }).execute().data

        if not step_row:
            continue
        new_step_id = step_row[0]["id"]

        for task in step.get("tasks", []):
            db.table("workflow_tasks").insert({
                "step_id": new_step_id,
                "name": task["name"],
                "description": task.get("description"),
                "input_type": task.get("input_type", "approval"),
                "assigned_to_role": task.get("assigned_to_role", "reviewer"),
                "reviewer_emails": task.get("reviewer_emails", []),
                "depends_on": task.get("depends_on", []),
                "template_url": task.get("template_url"),
                "versioned": task.get("versioned", False),
                "task_config": task.get("task_config", {}),
            }).execute()

    return get_template(new_id, org_id)


@router.post("/templates/{template_id}/promote")
def promote_template(template_id: str, org_id: str = Depends(get_current_org)):
    """
    Convierte una copia privada (creada al editar un workflow puntual) en una plantilla
    reutilizable del catálogo. No duplica nada — simplemente la hace visible en Plantillas.
    """
    db = _db()
    existing = db.table("workflow_templates").select("id").eq("id", template_id).eq("org_id", org_id).single().execute().data
    if not existing:
        raise HTTPException(404, "Template not found")
    db.table("workflow_templates").update({"is_catalog": True}).eq("id", template_id).execute()
    return get_template(template_id, org_id)


# ── Creación desde lenguaje natural ──────────────────────────────────────────

@router.post("/templates/from-description")
def create_template_from_nl(body: NLTemplateCreate, org_id: str = Depends(get_current_org)):
    """
    Genera un workflow_template completo desde una descripción en lenguaje natural.
    El LLM interpreta la descripción y genera steps + tasks estructurados.
    """
    import httpx, json

    system_prompt = """Eres un experto en diseño de procesos de negocio.
El usuario te describe un proceso en lenguaje natural y tú lo conviertes en una estructura JSON de workflow.

Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{
  "name": "Nombre del proceso",
  "description": "Descripción breve",
  "steps": [
    {
      "name": "Nombre del paso",
      "description": "Qué pasa en este paso",
      "step_order": 1,
      "type": "human_internal|human_external|agent|integration|condition|wait",
      "assigned_to": "email o rol (si aplica)",
      "advance_condition": "all_approved|any_approved|expression",
      "condition_expr": "expresión si advance_condition=expression o si hay condición de entrada",
      "deadline_offset_days": null,
      "tasks": [
        {
          "name": "Nombre de la tarea",
          "description": "Qué debe hacer",
          "input_type": "approval|file|text|number|date|signature",
          "assigned_to_role": "reviewer|applicant|agent",
          "reviewer_emails": []
        }
      ]
    }
  ]
}

Reglas:
- Si alguien externo debe revisar o firmar: type="human_external", assigned_to_role="reviewer"
- Si un usuario interno del sistema debe aprobar: type="human_internal"
- Si lo hace automáticamente un agente IA: type="agent"
- Si hay una condición ("si el monto > X"): agrega un step type="condition" con condition_expr
- Numera los steps desde 1
- Cada paso debe tener al menos una tarea
- Si se menciona firma electrónica: input_type="signature"
- Si se menciona subir documento: input_type="file"
- Responde solo el JSON, sin explicaciones ni markdown"""

    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-sonnet-4-5").replace("anthropic/", ""),
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [{"role": "user", "content": body.description}],
        },
        timeout=30,
    )
    r.raise_for_status()

    text = r.json()["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        generated = json.loads(text)
    except Exception:
        raise HTTPException(422, f"LLM returned invalid JSON: {text[:200]}")

    # Usar el endpoint de creación estructurada
    template_body = TemplateCreate(
        name=generated.get("name", "Workflow"),
        description=generated.get("description", ""),
        trigger_type=body.trigger_type,
        steps=[StepCreate(**s) for s in generated.get("steps", [])],
    )
    if body.dry_run:
        # Solo interpretación: el frontend muestra "así lo interpreté" y confirma antes de crear
        return template_body.model_dump()
    return create_template(template_body, org_id)


# ── Runs ─────────────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(org_id: str = Depends(get_current_org), stream_id: Optional[str] = None, status: Optional[str] = None):
    db = _db()
    q = db.table("workflow_runs").select("*, workflow_templates(name)").eq("org_id", org_id)
    if stream_id:
        q = q.eq("stream_id", stream_id)
    if status:
        q = q.eq("status", status)
    res = q.order("started_at", desc=True).limit(50).execute()
    return res.data or []


@router.post("/runs")
def start_run(body: RunStart, org_id: str = Depends(get_current_org)):
    from core.workflow_engine import start_run as engine_start
    try:
        run = engine_start(
            template_id=body.template_id,
            org_id=org_id,
            input_data=body.input_data,
            stream_id=body.stream_id,
            name=body.name,
            anchor_date=body.anchor_date,
            created_by=body.created_by,
        )
        return run
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/runs/{run_id}")
def get_run(run_id: str, org_id: str = Depends(get_current_org)):
    """Retorna el estado completo del run con todos sus steps y tareas."""
    db = _db()

    run = db.table("workflow_runs").select("*, workflow_templates(name, description)").eq("id", run_id).eq("org_id", org_id).single().execute().data
    if not run:
        raise HTTPException(404, "Run not found")

    steps = db.table("workflow_run_steps").select("*, workflow_steps(name, description, type, step_order)").eq("run_id", run_id).order("id").execute().data or []

    for step in steps:
        tasks = db.table("workflow_run_tasks").select("*, workflow_tasks(name, description, input_type)").eq("run_step_id", step["id"]).execute().data or []
        # Agregar reviewer links para tareas externas
        for task in tasks:
            if task.get("type") == "reviewer":
                links = db.table("reviewer_links").select("token, reviewer_email, expires_at, used_at").eq("run_task_id", task["id"]).execute().data or []
                task["reviewer_links"] = links
        step["tasks"] = tasks

    # Completar el roadmap: agregar los steps del template que todavía no se han
    # instanciado (status="not_started"), para que el frontend muestre el proceso completo.
    started_step_ids = {s["step_id"] for s in steps}
    template_steps = (
        db.table("workflow_steps")
        .select("id, name, description, type, step_order")
        .eq("template_id", run["template_id"])
        .order("step_order")
        .execute()
        .data or []
    )
    for tstep in template_steps:
        if tstep["id"] in started_step_ids:
            continue
        steps.append({
            "id": None,
            "step_id": tstep["id"],
            "status": "not_started",
            "started_at": None,
            "completed_at": None,
            "output_data": {},
            "deadline_at": None,
            "workflow_steps": {
                "name": tstep["name"],
                "description": tstep.get("description"),
                "type": tstep["type"],
                "step_order": tstep["step_order"],
            },
            "tasks": [],
        })

    # Ordenar todo el roadmap por step_order del template
    steps.sort(key=lambda s: s.get("workflow_steps", {}).get("step_order", 0))

    run["steps"] = steps
    return run


@router.get("/runs/{run_id}/timeline")
def get_run_timeline(run_id: str, org_id: str = Depends(get_current_org)):
    """Vista simplificada para el widget de stream."""
    db = _db()

    run = db.table("workflow_runs").select("id, name, status, started_at, completed_at, workflow_templates(name)").eq("id", run_id).eq("org_id", org_id).single().execute().data
    if not run:
        raise HTTPException(404, "Run not found")

    steps = db.table("workflow_run_steps").select("status, started_at, completed_at, deadline_at, output_data, workflow_steps(name, step_order, type)").eq("run_id", run_id).order("id").execute().data or []

    total = len(steps)
    completed = sum(1 for s in steps if s["status"] == "completed")

    return {
        "run_id": run_id,
        "name": run.get("name") or run.get("workflow_templates", {}).get("name", "Workflow"),
        "status": run["status"],
        "progress": f"{completed}/{total}",
        "started_at": run["started_at"],
        "completed_at": run.get("completed_at"),
        "steps": [
            {
                "name": s.get("workflow_steps", {}).get("name", ""),
                "order": s.get("workflow_steps", {}).get("step_order", 0),
                "type": s.get("workflow_steps", {}).get("type", ""),
                "status": s["status"],
                "started_at": s.get("started_at"),
                "completed_at": s.get("completed_at"),
                "deadline_at": s.get("deadline_at"),
            }
            for s in steps
        ],
    }


# ── Completar tareas ──────────────────────────────────────────────────────────

@router.post("/tasks/complete")
def complete_task(body: TaskComplete, org_id: str = Depends(get_current_org)):
    from core.workflow_engine import complete_task as engine_complete
    try:
        return engine_complete(
            run_task_id=body.run_task_id,
            response_data=body.response_data,
            actor=body.actor,
            org_id=org_id,
            status=body.status,
            rejection_reason=body.rejection_reason,
        )
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Acceso externo (revisores) ────────────────────────────────────────────────

@router.get("/review/{token}")
def get_review_task(token: str):
    """
    Endpoint público para revisores externos.
    Retorna solo la información necesaria para completar su tarea.
    No requiere X-Org-Id — el token contiene toda la info necesaria.
    """
    db = _db()

    link = db.table("reviewer_links").select("*, workflow_run_tasks(*, workflow_tasks(name, description, input_type, task_config), run_step_id)").eq("token", token).single().execute().data
    if not link:
        raise HTTPException(404, "Link not found or expired")

    from datetime import datetime, timezone
    if link.get("used_at"):
        raise HTTPException(410, "This link has already been used")
    if link["expires_at"] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(410, "This link has expired")

    run_task = link.get("workflow_run_tasks", {}) or {}
    task_def = run_task.get("workflow_tasks", {}) or {}

    # Obtener contexto mínimo del run (sin info sensible)
    run_step = db.table("workflow_run_steps").select("run_id, output_data").eq("id", run_task.get("run_step_id")).single().execute().data or {}
    run = db.table("workflow_runs").select("name, input_data").eq("id", run_step.get("run_id")).single().execute().data or {}

    return {
        "token": token,
        "reviewer_email": link["reviewer_email"],
        "expires_at": link["expires_at"],
        "run_task_id": run_task.get("id"),
        "task": {
            "name": task_def.get("name", ""),
            "description": task_def.get("description", ""),
            "input_type": task_def.get("input_type", "approval"),
            "config": task_def.get("task_config", {}),
        },
        "context": {
            "process_name": run.get("name", ""),
            "applicant_data": run.get("input_data", {}),
            "previous_step_output": run_step.get("output_data", {}),
        },
    }


@router.post("/review/{token}/submit")
def submit_review(token: str, body: dict):
    """Revisor externo completa su tarea via token."""
    db = _db()

    link = db.table("reviewer_links").select("*").eq("token", token).single().execute().data
    if not link:
        raise HTTPException(404, "Link not found")
    if link.get("used_at"):
        raise HTTPException(410, "Already used")

    from datetime import datetime, timezone
    if link["expires_at"] < datetime.now(timezone.utc).isoformat():
        raise HTTPException(410, "Link expired")

    # Validar email (seguridad básica)
    submitted_email = body.get("reviewer_email", "")
    if submitted_email.lower() != link["reviewer_email"].lower():
        raise HTTPException(403, "Email mismatch")

    # Marcar link como usado
    db.table("reviewer_links").update({"used_at": datetime.now(timezone.utc).isoformat()}).eq("id", link["id"]).execute()

    # Obtener org_id para el engine
    run_task = db.table("workflow_run_tasks").select("run_step_id").eq("id", link["run_task_id"]).single().execute().data
    run_step = db.table("workflow_run_steps").select("run_id").eq("id", run_task["run_step_id"]).single().execute().data
    run = db.table("workflow_runs").select("org_id").eq("id", run_step["run_id"]).single().execute().data

    from core.workflow_engine import complete_task as engine_complete
    return engine_complete(
        run_task_id=link["run_task_id"],
        response_data=body.get("response_data", {}),
        actor=submitted_email,
        org_id=run["org_id"],
        status=body.get("status", "approved"),
        rejection_reason=body.get("rejection_reason"),
    )


# ── Seed por módulo ───────────────────────────────────────────────────────────

@router.post("/modules/{module_key}/seed")
def seed_module_workflows_endpoint(module_key: str, org_id: str = Depends(get_current_org)):
    """Crea las plantillas por defecto de un módulo si aún no existen."""
    created = seed_module_workflows(org_id, module_key)
    return {"module_key": module_key, "created": created}


# ── Scheduler / eventos del sistema ───────────────────────────────────────────

@router.post("/scheduler/tick")
def scheduler_tick(body: SchedulerTick, org_id: str = Depends(get_current_org)):
    """Ejecuta un tick del scheduler para disparar flujos automáticos."""
    if body.module_key and body.module_key not in ("collections", "crm", "inventory", "accounting", "chief_of_staff"):
        raise HTTPException(400, f"Scheduler no implementado para módulo {body.module_key}")
    result = tick_org(org_id, module_key=body.module_key)
    return result


# ── Eventos (audit log) ───────────────────────────────────────────────────────

@router.get("/runs/{run_id}/events")
def get_run_events(run_id: str, org_id: str = Depends(get_current_org)):
    res = _db().table("workflow_events").select("*").eq("run_id", run_id).eq("org_id", org_id).order("created_at").execute()
    return res.data or []
