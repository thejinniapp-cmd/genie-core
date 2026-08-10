"""core/workflow_engine.py — Motor de ejecución de workflows"""
import logging
import threading
from datetime import datetime, timezone
from typing import Optional
import os
from supabase import create_client
from simpleeval import SimpleEval

from core.workflows.actions import run_action

log = logging.getLogger("genie.workflow_engine")


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ── Audit ─────────────────────────────────────────────────────────────────────

def log_event(org_id: str, run_id: str, event_type: str, actor: str, payload: dict = {}):
    try:
        _db().table("workflow_events").insert({
            "org_id": org_id,
            "run_id": run_id,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
        }).execute()
    except Exception as e:
        log.warning(f"[workflow_events] Could not log event: {e}")


# ── Evaluación de condiciones ─────────────────────────────────────────────────

def evaluate_condition(expr: str, context: dict) -> bool:
    """
    Evalúa una expresión simple de condición de avance.
    Contexto: output del step anterior disponible como 'output'.
    Ejemplos: "output.approved == true", "output.max_rpn < 100"
    """
    if not expr:
        return True
    try:
        evaluator = SimpleEval(names={
            "output": _DotDict(context.get("output", {})),
            "input": _DotDict(context.get("input", {})),
            "true": True,
            "false": False,
            "null": None,
        })
        return bool(evaluator.eval(expr))
    except Exception as e:
        log.warning(f"[condition_eval] Error evaluating '{expr}': {e}")
        return False


class _DotDict:
    """Permite acceso a dict via punto: output.approved"""
    def __init__(self, d: dict):
        self._d = d or {}

    def __getattr__(self, key):
        val = self._d.get(key)
        if isinstance(val, dict):
            return _DotDict(val)
        return val

    def __getitem__(self, key):
        return self._d.get(key)


# ── Lógica de avance ──────────────────────────────────────────────────────────

def check_step_can_advance(run_step_id: str) -> bool:
    """
    Verifica si todas las tareas del step cumplen la condición de avance.
    Retorna True si el step está listo para completarse.
    """
    db = _db()

    run_step = db.table("workflow_run_steps").select("*, workflow_steps(advance_condition, condition_expr)").eq("id", run_step_id).single().execute().data
    if not run_step:
        return False

    tasks = db.table("workflow_run_tasks").select("status, response_data").eq("run_step_id", run_step_id).execute().data or []
    if not tasks:
        return True

    step_config = run_step.get("workflow_steps", {}) or {}
    advance_condition = step_config.get("advance_condition", "all_approved")

    if advance_condition == "all_approved":
        return all(t["status"] in ("approved", "completed") for t in tasks)

    if advance_condition == "any_approved":
        return any(t["status"] in ("approved", "completed") for t in tasks)

    if advance_condition == "expression":
        expr = step_config.get("condition_expr", "")
        # Agregar outputs de todas las tareas al contexto
        combined_output = {}
        for t in tasks:
            combined_output.update(t.get("response_data") or {})
        return evaluate_condition(expr, {"output": combined_output})

    return False


def advance_run(run_id: str, org_id: str):
    """
    Lógica principal de avance: completa el step actual y activa el siguiente.
    Se llama cada vez que una tarea se completa/aprueba.
    """
    db = _db()

    # Obtener run y template
    run = db.table("workflow_runs").select("*").eq("id", run_id).single().execute().data
    if not run or run["status"] in ("completed", "failed", "cancelled"):
        return

    # Encontrar el step en progreso
    current_step_row = (
        db.table("workflow_run_steps")
        .select("*")
        .eq("run_id", run_id)
        .eq("status", "in_progress")
        .order("id")
        .limit(1)
        .execute()
        .data
    )
    if not current_step_row:
        return
    current_step = current_step_row[0]

    # Verificar si puede avanzar
    if not check_step_can_advance(current_step["id"]):
        return

    # Recopilar output de todas las tareas del step actual
    tasks = db.table("workflow_run_tasks").select("response_data").eq("run_step_id", current_step["id"]).execute().data or []
    combined_output = {}
    for t in tasks:
        combined_output.update(t.get("response_data") or {})

    # Completar el step actual
    db.table("workflow_run_steps").update({
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "output_data": combined_output,
    }).eq("id", current_step["id"]).execute()

    log_event(org_id, run_id, "step.completed", "system", {"step_id": current_step["step_id"]})

    # Buscar el siguiente step del template
    current_template_step = db.table("workflow_steps").select("step_order, template_id").eq("id", current_step["step_id"]).single().execute().data
    if not current_template_step:
        return

    next_template_step = (
        db.table("workflow_steps")
        .select("*")
        .eq("template_id", current_template_step["template_id"])
        .gt("step_order", current_template_step["step_order"])
        .order("step_order")
        .limit(1)
        .execute()
        .data
    )

    if not next_template_step:
        # No hay más steps — workflow completado
        db.table("workflow_runs").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id).execute()
        log_event(org_id, run_id, "run.completed", "system")
        return

    next_step_def = next_template_step[0]

    # Verificar condición de entrada del siguiente step (si tiene)
    entry_condition = next_step_def.get("condition_expr", "")
    if entry_condition:
        context = {"output": combined_output, "input": run.get("input_data", {})}
        if not evaluate_condition(entry_condition, context):
            # Skip este step y avanzar al siguiente
            skip_row = db.table("workflow_run_steps").insert({
                "run_id": run_id,
                "step_id": next_step_def["id"],
                "status": "skipped",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).execute().data
            log_event(org_id, run_id, "step.skipped", "system", {"step_id": next_step_def["id"]})
            advance_run(run_id, org_id)  # recursivo para ir al siguiente
            return

    # Activar el siguiente step
    deadline_at = None
    if run.get("anchor_date") and next_step_def.get("deadline_offset_days") is not None:
        from datetime import timedelta, date
        anchor = date.fromisoformat(run["anchor_date"])
        deadline = anchor + timedelta(days=next_step_def["deadline_offset_days"])
        deadline_at = deadline.isoformat()

    new_step_row = db.table("workflow_run_steps").insert({
        "run_id": run_id,
        "step_id": next_step_def["id"],
        "status": "in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "deadline_at": deadline_at,
    }).execute().data

    if not new_step_row:
        return
    new_step_id = new_step_row[0]["id"]

    log_event(org_id, run_id, "step.started", "system", {"step_id": next_step_def["id"]})

    # Crear las tareas del nuevo step
    _create_run_tasks(db, new_step_id, next_step_def["id"], run_id, org_id, combined_output)

    # Si es un step de agente, dispararlo automáticamente
    if next_step_def["type"] == "agent":
        _run_agent_step(run_id, new_step_id, next_step_def, combined_output, org_id)

    # Si es un step de acción determinista, ejecutarlo
    if next_step_def["type"] == "action":
        _run_action_step(run_id, new_step_id, next_step_def, combined_output, org_id)


def _create_run_tasks(db, run_step_id: str, step_id: str, run_id: str, org_id: str, prev_output: dict):
    """Crea las instancias de tareas para un step recién activado."""
    tasks = db.table("workflow_tasks").select("*").eq("step_id", step_id).execute().data or []

    for task in tasks:
        reviewer_emails = task.get("reviewer_emails") or []
        assigned_to = reviewer_emails[0] if reviewer_emails else task.get("assigned_to_role")

        task_row = db.table("workflow_run_tasks").insert({
            "run_step_id": run_step_id,
            "task_id": task["id"],
            "assigned_to": assigned_to,
            "type": task.get("assigned_to_role", "human_internal"),
            "status": "pending",
        }).execute().data

        if not task_row:
            continue
        run_task_id = task_row[0]["id"]

        # Si es revisor externo, crear reviewer_link para cada email
        if task.get("assigned_to_role") == "reviewer" and reviewer_emails:
            for email in reviewer_emails:
                db.table("reviewer_links").insert({
                    "run_task_id": run_task_id,
                    "reviewer_email": email,
                }).execute()
                log.info(f"[workflow] Reviewer link created for {email}, task {run_task_id}")


def _run_agent_step(run_id: str, run_step_id: str, step_def: dict, prev_output: dict, org_id: str):
    """Dispara el agente IA para un step de tipo 'agent' en background."""
    def _worker():
        try:
            db = _db()
            run = db.table("workflow_runs").select("stream_id, input_data").eq("id", run_id).single().execute().data
            if not run:
                return

            agent_id = step_def.get("config", {}).get("agent_id") or step_def.get("agent_id")
            agent_config = {}
            if agent_id:
                agent_row = db.table("agents").select("*").eq("id", agent_id).single().execute().data
                if agent_row:
                    agent_config = {
                        "system_prompt": agent_row.get("system_prompt", ""),
                        "model_id": agent_row.get("model_id", os.environ.get("GENIE_DEFAULT_MODEL", "anthropic/claude-sonnet-4-5")),
                        "temperature": agent_row.get("temperature", 0.3),
                        "max_tokens": agent_row.get("max_tokens", 2048),
                        "tools": agent_row.get("tools", []),
                    }

            # Construir prompt con contexto del workflow
            task_instruction = step_def.get("config", {}).get("instruction", step_def.get("name", "Completa esta tarea del workflow."))
            context_text = f"Tarea del workflow: {task_instruction}\n\nContexto del paso anterior:\n{prev_output}"

            from agents.prompt_agent import PromptAgent
            agent = PromptAgent()
            fake_job = {
                "id": f"wf-{run_step_id}",
                "org_id": org_id,
                "stream_id": run.get("stream_id"),
                "agent_config": agent_config,
                "input_data": {"message": context_text, "stream_id": run.get("stream_id")},
                "attempt": 1,
            }
            agent.run(fake_job, org_id)

            # Marcar la tarea del agente como completada
            run_tasks = db.table("workflow_run_tasks").select("id").eq("run_step_id", run_step_id).execute().data or []
            for t in run_tasks:
                db.table("workflow_run_tasks").update({
                    "status": "completed",
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "response_data": {"completed_by": "agent"},
                }).eq("id", t["id"]).execute()

            log_event(org_id, run_id, "task.completed", f"agent:{agent_id or 'default'}")
            advance_run(run_id, org_id)

        except Exception as e:
            log.error(f"[workflow_agent_step] Error: {e}", exc_info=True)

    threading.Thread(target=_worker, daemon=True).start()


def _run_action_step(run_id: str, run_step_id: str, step_def: dict, prev_output: dict, org_id: str):
    """Ejecuta un step determinista de tipo 'action' en background."""
    def _worker():
        try:
            db = _db()
            run = db.table("workflow_runs").select("input_data").eq("id", run_id).single().execute().data
            input_data = run.get("input_data") or {} if run else {}

            action_name = step_def.get("config", {}).get("action")
            log.info(f"[workflow_action] Running action '{action_name}' for run {run_id}")
            output = run_action(org_id, action_name, input_data)

            run_tasks = db.table("workflow_run_tasks").select("id").eq("run_step_id", run_step_id).execute().data or []
            for t in run_tasks:
                db.table("workflow_run_tasks").update({
                    "status": "completed",
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "response_data": output,
                }).eq("id", t["id"]).execute()

            log_event(org_id, run_id, "task.completed", f"action:{action_name}", {"output": output})
            advance_run(run_id, org_id)
        except Exception as e:
            log.error(f"[workflow_action_step] Error: {e}", exc_info=True)

    threading.Thread(target=_worker, daemon=True).start()


# ── Iniciar un run ────────────────────────────────────────────────────────────

def start_run(template_id: str, org_id: str, input_data: dict, stream_id: str = None,
              name: str = None, anchor_date: str = None, created_by: str = None,
              metadata: dict = None) -> dict:
    """Crea e inicia una instancia de workflow."""
    db = _db()

    # Verificar que el template existe y está activo
    template = db.table("workflow_templates").select("*").eq("id", template_id).eq("org_id", org_id).eq("is_active", True).single().execute().data
    if not template:
        raise ValueError(f"Template {template_id} not found or inactive")

    # Crear el run
    run_row = db.table("workflow_runs").insert({
        "template_id": template_id,
        "org_id": org_id,
        "stream_id": stream_id,
        "name": name or template["name"],
        "status": "running",
        "input_data": input_data,
        "anchor_date": anchor_date,
        "created_by": created_by,
        "metadata": metadata or input_data,
    }).execute().data

    if not run_row:
        raise RuntimeError("Could not create workflow run")
    run = run_row[0]
    run_id = run["id"]

    log_event(org_id, run_id, "run.started", created_by or "system", {"template_id": template_id, "input_data": input_data})

    # Obtener el primer step del template
    first_step = (
        db.table("workflow_steps")
        .select("*")
        .eq("template_id", template_id)
        .order("step_order")
        .limit(1)
        .execute()
        .data
    )
    if not first_step:
        raise RuntimeError("Template has no steps")

    first_step_def = first_step[0]

    # Calcular deadline si aplica
    deadline_at = None
    if anchor_date and first_step_def.get("deadline_offset_days") is not None:
        from datetime import timedelta, date
        anchor = date.fromisoformat(anchor_date)
        deadline = anchor + timedelta(days=first_step_def["deadline_offset_days"])
        deadline_at = deadline.isoformat()

    # Crear el primer run_step
    step_row = db.table("workflow_run_steps").insert({
        "run_id": run_id,
        "step_id": first_step_def["id"],
        "status": "in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "deadline_at": deadline_at,
    }).execute().data

    if not step_row:
        raise RuntimeError("Could not create first run step")
    run_step_id = step_row[0]["id"]

    log_event(org_id, run_id, "step.started", "system", {"step_id": first_step_def["id"]})

    # Crear las tareas del primer step
    _create_run_tasks(db, run_step_id, first_step_def["id"], run_id, org_id, input_data)

    # Si el primer step es de agente, dispararlo
    if first_step_def["type"] == "agent":
        _run_agent_step(run_id, run_step_id, first_step_def, input_data, org_id)

    # Si el primer step es de acción determinista, ejecutarlo
    if first_step_def["type"] == "action":
        _run_action_step(run_id, run_step_id, first_step_def, input_data, org_id)

    return run


def complete_task(run_task_id: str, response_data: dict, actor: str, org_id: str,
                  status: str = "approved", rejection_reason: str = None) -> dict:
    """
    El actor (humano o agente) completa su tarea.
    Dispara el avance automático si corresponde.
    """
    db = _db()

    update = {
        "status": status,
        "response_data": response_data,
    }
    if status == "approved":
        update["approved_at"] = datetime.now(timezone.utc).isoformat()
    elif status == "rejected":
        update["rejected_at"] = datetime.now(timezone.utc).isoformat()
        if rejection_reason:
            update["rejection_reason"] = rejection_reason

    db.table("workflow_run_tasks").update(update).eq("id", run_task_id).execute()

    # Guardar respuesta detallada
    db.table("run_task_responses").insert({
        "run_task_id": run_task_id,
        "response_data": response_data,
        "submitted_by": actor,
    }).execute()

    # Obtener el run para el audit log
    run_task = db.table("workflow_run_tasks").select("run_step_id").eq("id", run_task_id).single().execute().data
    run_step = db.table("workflow_run_steps").select("run_id").eq("id", run_task["run_step_id"]).single().execute().data
    run_id = run_step["run_id"]
    run = db.table("workflow_runs").select("org_id").eq("id", run_id).single().execute().data

    log_event(run["org_id"], run_id, f"task.{status}", actor, {"run_task_id": run_task_id, "response": response_data})

    # Intentar avanzar el workflow
    advance_run(run_id, run["org_id"])

    return {"run_task_id": run_task_id, "status": status}
