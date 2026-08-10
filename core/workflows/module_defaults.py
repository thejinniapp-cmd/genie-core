"""core/workflows/module_defaults.py — Plantillas de workflow por módulo/app"""
import logging
from typing import List, Optional

from supabase import create_client
import os

log = logging.getLogger("genie.module_defaults")

DEFAULT_MODULE_WORKFLOWS = {
    "inventory": [
        {
            "name": "Stock bajo - orden de compra",
            "description": "Detecta artículos con stock bajo y genera una orden de compra borrador.",
            "trigger_type": "event",
            "trigger_config": {"event": "low_stock"},
            "steps": [
                {
                    "name": "Generar orden de compra",
                    "description": "Crea una orden de compra borrador para reabastecer el artículo.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "inventory_low_stock_po"},
                    "tasks": [
                        {
                            "name": "Crear orden de compra",
                            "description": "Acción automática de inventario",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
    ],
    "chief_of_staff": [
        {
            "name": "Resumen ejecutivo diario",
            "description": "Envía un resumen ejecutivo automático al stream principal cada día.",
            "trigger_type": "event",
            "trigger_config": {"event": "daily_summary"},
            "steps": [
                {
                    "name": "Generar y enviar resumen",
                    "description": "Recopila métricas clave y publica resumen ejecutivo.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "chief_of_staff_daily_summary"},
                    "tasks": [
                        {
                            "name": "Enviar resumen",
                            "description": "Acción automática del Chief of Staff",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
        {
            "name": "Orquestación diaria del Chief of Staff",
            "description": "Revisa el negocio y emite órdenes a los agentes de cada módulo.",
            "trigger_type": "event",
            "trigger_config": {"event": "orchestrate_daily"},
            "steps": [
                {
                    "name": "Generar resumen y dispatch de comandos",
                    "description": "Publica resumen ejecutivo y ordena acciones a los agentes.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "chief_of_staff_orchestrate"},
                    "tasks": [
                        {
                            "name": "Orquestar agentes",
                            "description": "Acción automática del Chief of Staff",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
    ],
    "accounting": [
        {
            "name": "Ingreso automático por pago",
            "description": "Al recibir un pago, registra automáticamente el ingreso contable.",
            "trigger_type": "event",
            "trigger_config": {"event": "payment_received_accounting"},
            "steps": [
                {
                    "name": "Registrar ingreso contable",
                    "description": "Crea una transacción de ingreso vinculada al pago.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "payment_received_accounting"},
                    "tasks": [
                        {
                            "name": "Crear transacción de ingreso",
                            "description": "Acción automática de contabilidad",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
        {
            "name": "Cierre mensual contable",
            "description": "Genera resumen mensual y tarea de cierre contable.",
            "trigger_type": "event",
            "trigger_config": {"event": "monthly_close"},
            "steps": [
                {
                    "name": "Generar resumen y tarea de cierre",
                    "description": "Resume ingresos/gastos del mes y crea tarea.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "accounting_monthly_close"},
                    "tasks": [
                        {
                            "name": "Generar cierre mensual",
                            "description": "Acción automática de contabilidad",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
    ],
    "crm": [
        {
            "name": "Seguimiento a nuevo contacto",
            "description": "Crea una tarea de seguimiento cuando se registra un contacto.",
            "trigger_type": "event",
            "trigger_config": {"event": "new_contact"},
            "steps": [
                {
                    "name": "Crear tarea de seguimiento",
                    "description": "Genera actividad de seguimiento para el nuevo contacto.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "crm_new_contact_followup"},
                    "tasks": [
                        {
                            "name": "Ejecutar seguimiento",
                            "description": "Crear actividad CRM",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
        {
            "name": "Seguimiento a nuevo deal",
            "description": "Crea una tarea de seguimiento cuando se crea una oportunidad.",
            "trigger_type": "event",
            "trigger_config": {"event": "new_deal"},
            "steps": [
                {
                    "name": "Crear tarea de seguimiento",
                    "description": "Genera actividad de seguimiento para el nuevo deal.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "crm_new_deal_followup"},
                    "tasks": [
                        {
                            "name": "Ejecutar seguimiento",
                            "description": "Crear actividad CRM",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
        {
            "name": "Reactivar deals estancados",
            "description": "Detecta deals abiertos sin actividad reciente y genera tarea de reactivación.",
            "trigger_type": "event",
            "trigger_config": {"event": "stale_deal"},
            "steps": [
                {
                    "name": "Crear tarea de reactivación",
                    "description": "Genera actividad para reactivar el deal.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "crm_stale_deal_nurture"},
                    "tasks": [
                        {
                            "name": "Ejecutar reactivación",
                            "description": "Crear actividad CRM",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
    ],
    "collections": [
        {
            "name": "Cobranza vencida",
            "description": "Detecta facturas vencidas y genera una actividad de cobro en CRM.",
            "trigger_type": "event",
            "trigger_config": {"event": "invoice_overdue"},
            "steps": [
                {
                    "name": "Crear actividad de cobro",
                    "description": "Crea una tarea de seguimiento en CRM para cobrar la factura.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "collections_overdue_activity"},
                    "tasks": [
                        {
                            "name": "Ejecutar acción de cobro",
                            "description": "Acción automática de cobranza",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
        {
            "name": "Pago recibido",
            "description": "Al recibir un pago, registra agradecimiento y actualiza seguimiento.",
            "trigger_type": "event",
            "trigger_config": {"event": "payment_received"},
            "steps": [
                {
                    "name": "Confirmar pago y registrar seguimiento",
                    "description": "Crea una nota en CRM con el saldo restante.",
                    "step_order": 1,
                    "type": "action",
                    "advance_condition": "all_approved",
                    "config": {"action": "payment_received_ack"},
                    "tasks": [
                        {
                            "name": "Registrar pago",
                            "description": "Acción automática de confirmación de pago",
                            "input_type": "text",
                            "assigned_to_role": "agent",
                            "task_config": {},
                        }
                    ],
                }
            ],
        },
    ],
}


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _template_exists(org_id: str, name: str) -> bool:
    try:
        row = (
            _db()
            .table("workflow_templates")
            .select("id")
            .eq("org_id", org_id)
            .eq("name", name)
            .eq("is_active", True)
            .maybe_single()
            .execute()
            .data
        )
        return bool(row)
    except Exception as e:
        log.warning(f"[module_defaults] Could not check template existence: {e}")
        return False


def _create_template(org_id: str, module_key: str, definition: dict) -> Optional[str]:
    db = _db()

    tmpl = db.table("workflow_templates").insert({
        "org_id": org_id,
        "name": definition["name"],
        "description": definition.get("description"),
        "trigger_type": definition.get("trigger_type", "event"),
        "trigger_config": {
            **definition.get("trigger_config", {}),
            "module_key": module_key,
        },
        "has_anchor_date": False,
        "is_catalog": True,
        "is_active": True,
    }).execute().data

    if not tmpl:
        log.error(f"[module_defaults] Could not create template {definition['name']}")
        return None
    template_id = tmpl[0]["id"]

    for step in definition.get("steps", []):
        step_row = db.table("workflow_steps").insert({
            "template_id": template_id,
            "name": step["name"],
            "description": step.get("description"),
            "step_order": step["step_order"],
            "type": step.get("type", "action"),
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
        step_id = step_row[0]["id"]

        for task in step.get("tasks", []):
            db.table("workflow_tasks").insert({
                "step_id": step_id,
                "name": task.get("name", "Tarea"),
                "description": task.get("description"),
                "input_type": task.get("input_type", "text"),
                "assigned_to_role": task.get("assigned_to_role", "agent"),
                "reviewer_emails": task.get("reviewer_emails", []),
                "depends_on": task.get("depends_on", []),
                "template_url": task.get("template_url"),
                "versioned": task.get("versioned", False),
                "task_config": task.get("task_config", {}),
            }).execute()

    return template_id


def seed_module_workflows(org_id: str, module_key: str) -> List[dict]:
    """Crea las plantillas por defecto del módulo si no existen."""
    definitions = DEFAULT_MODULE_WORKFLOWS.get(module_key, [])
    created = []
    for definition in definitions:
        if _template_exists(org_id, definition["name"]):
            continue
        template_id = _create_template(org_id, module_key, definition)
        if template_id:
            created.append({"template_id": template_id, "name": definition["name"]})
    return created


def get_module_template_by_event(org_id: str, module_key: str, event: str) -> Optional[dict]:
    """Obtiene una plantilla activa de un módulo para un evento dado."""
    try:
        rows = (
            _db()
            .table("workflow_templates")
            .select("*")
            .eq("org_id", org_id)
            .eq("is_active", True)
            .eq("trigger_type", "event")
            .execute()
            .data
            or []
        )
        for row in rows:
            cfg = row.get("trigger_config") or {}
            if cfg.get("event") == event and cfg.get("module_key") == module_key:
                return row
        return None
    except Exception as e:
        log.warning(f"[module_defaults] Could not fetch template: {e}")
        return None
