"""core/workflows/scheduler.py — Disparador de flujos por eventos del sistema"""
import logging
from datetime import datetime, timezone, date, timedelta
from typing import List, Optional

from supabase import create_client
import os

from core.workflow_engine import start_run
from core.workflows.module_defaults import get_module_template_by_event
from core.workflows.chief_alerts import check_and_send_alerts
from core.workflows.agent_commands import (
    get_pending_commands,
    mark_command_processed,
    list_agent_keys,
    is_ai_staff_key,
)
from core.workflows.ai_staff_runner import run_ai_staff_command

log = logging.getLogger("genie.workflow_scheduler")


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _module_enabled(org_id: str, module_key: str) -> bool:
    try:
        row = (
            _db()
            .table("organization_modules")
            .select("enabled")
            .eq("organization_id", org_id)
            .eq("module_key", module_key)
            .maybe_single()
            .execute()
            .data
        )
        return bool(row and row.get("enabled"))
    except Exception as e:
        log.warning(f"[scheduler] Could not check module {module_key}: {e}")
        return False


def _wait_for_run(run_id: str, timeout: float = 15.0) -> Optional[str]:
    """Espera a que un workflow run termine. Retorna status final o None."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            run = (
                _db()
                .table("workflow_runs")
                .select("status")
                .eq("id", run_id)
                .maybe_single()
                .execute()
                .data
            )
            if run and run.get("status") in ("completed", "failed", "cancelled"):
                return run["status"]
        except Exception as e:
            log.warning(f"[scheduler] Error waiting for run {run_id}: {e}")
        time.sleep(0.5)
    return None


def _module_tick_fn(module_key: str):
    mapping = {
        "collections": tick_collections,
        "crm": tick_crm,
        "inventory": tick_inventory,
        "accounting": tick_accounting,
        "control": tick_control,
        "cumple": tick_cumple,
    }
    return mapping.get(module_key)


def _recent_run_exists(org_id: str, template_id: str, invoice_id: str, days: int = 7) -> bool:
    """Evita duplicar runs para el mismo invoice en pocos días."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = (
            _db()
            .table("workflow_runs")
            .select("id, metadata")
            .eq("org_id", org_id)
            .eq("template_id", template_id)
            .gt("started_at", since)
            .execute()
            .data
            or []
        )
        for row in rows:
            meta = row.get("metadata") or {}
            if str(meta.get("invoice_id")) == str(invoice_id):
                return True
        return False
    except Exception as e:
        log.warning(f"[scheduler] Could not check recent runs: {e}")
        return False


def start_event_run(org_id: str, module_key: str, event: str, input_data: dict,
                    name: Optional[str] = None, metadata: Optional[dict] = None) -> Optional[dict]:
    """Inicia un workflow de evento si existe plantilla activa."""
    if not _module_enabled(org_id, module_key):
        return None

    template = get_module_template_by_event(org_id, module_key, event)
    if not template:
        log.info(f"[scheduler] No template found for {module_key}/{event}")
        return None

    try:
        run = start_run(
            template_id=template["id"],
            org_id=org_id,
            input_data=input_data,
            name=name or f"{template['name']}: {input_data.get('invoice_id', 'N/A')}",
            metadata=metadata or input_data,
        )
        return run
    except Exception as e:
        log.error(f"[scheduler] Could not start run for {module_key}/{event}: {e}", exc_info=True)
        return None


def tick_collections(org_id: str) -> List[dict]:
    """Revisa facturas vencidas y dispara workflow de cobranza + proyección semanal de flujo de caja."""
    if not _module_enabled(org_id, "collections"):
        return []

    started = []

    template = get_module_template_by_event(org_id, "collections", "invoice_overdue")
    if template:
        today_str = date.today().isoformat()
        invoices = (
            _db()
            .table("erp_invoices")
            .select("id, invoice_number, total, due_date, status")
            .eq("org_id", org_id)
            .in_("status", ["sent", "partial"])
            .lt("due_date", today_str)
            .execute()
            .data
            or []
        )
        for inv in invoices:
            invoice_id = inv["id"]
            if _recent_run_exists(org_id, template["id"], invoice_id, days=7):
                continue
            run = start_event_run(
                org_id=org_id,
                module_key="collections",
                event="invoice_overdue",
                input_data={"invoice_id": invoice_id},
                name=f"Cobranza vencida: {inv.get('invoice_number', 'S/N')}",
                metadata={"invoice_id": invoice_id},
            )
            if run:
                started.append({"invoice_id": invoice_id, "run_id": run.get("id")})

    forecast_template = get_module_template_by_event(org_id, "collections", "cash_weekly_forecast")
    if forecast_template:
        iso_year, iso_week, _ = date.today().isocalendar()
        if not _recent_run_for_week(org_id, forecast_template["id"], iso_year, iso_week):
            run = start_event_run(
                org_id=org_id,
                module_key="collections",
                event="cash_weekly_forecast",
                input_data={},
                name=f"Proyección semanal de flujo de caja - semana {iso_week}/{iso_year}",
                metadata={"iso_year": iso_year, "iso_week": iso_week},
            )
            if run:
                started.append({"run_id": run.get("id"), "event": "cash_weekly_forecast"})

    return started


def _recent_activity_exists(org_id: str, deal_id: str, days: int = 7) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        count = (
            _db()
            .table("crm_activities")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .eq("deal_id", deal_id)
            .gt("created_at", since)
            .execute()
            .count
            or 0
        )
        return count > 0
    except Exception as e:
        log.warning(f"[scheduler] Could not check recent activity: {e}")
        return False


def tick_crm(org_id: str) -> List[dict]:
    """Revisa deals abiertos sin actividad reciente y dispara workflow de reactivación."""
    if not _module_enabled(org_id, "crm"):
        return []

    template = get_module_template_by_event(org_id, "crm", "stale_deal")
    if not template:
        return []

    stale_before = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    deals = (
        _db()
        .table("crm_deals")
        .select("id, name, value")
        .eq("org_id", org_id)
        .eq("status", "open")
        .lt("updated_at", stale_before)
        .execute()
        .data
        or []
    )

    started = []
    for deal in deals:
        deal_id = deal["id"]
        if _recent_activity_exists(org_id, deal_id, days=7):
            continue
        if _recent_run_exists(org_id, template["id"], deal_id, days=7):
            continue
        run = start_event_run(
            org_id=org_id,
            module_key="crm",
            event="stale_deal",
            input_data={"deal_id": deal_id},
            name=f"Reactivar: {deal.get('name', 'Deal')}",
            metadata={"deal_id": deal_id},
        )
        if run:
            started.append({"deal_id": deal_id, "run_id": run.get("id")})
    return started


def _pending_po_for_item(org_id: str, item_id: str) -> bool:
    try:
        lines = (
            _db()
            .table("inventory_purchase_order_items")
            .select("order_id")
            .eq("org_id", org_id)
            .eq("item_id", item_id)
            .execute()
            .data
            or []
        )
        order_ids = [line["order_id"] for line in lines]
        if not order_ids:
            return False
        pos = (
            _db()
            .table("inventory_purchase_orders")
            .select("id")
            .eq("org_id", org_id)
            .in_("status", ["draft", "sent", "partial"])
            .in_("id", order_ids)
            .execute()
            .data
            or []
        )
        return len(pos) > 0
    except Exception as e:
        log.warning(f"[scheduler] Could not check pending POs: {e}")
        return False


def tick_inventory(org_id: str) -> List[dict]:
    """Revisa artículos con stock bajo y dispara workflow de orden de compra."""
    if not _module_enabled(org_id, "inventory"):
        return []

    template = get_module_template_by_event(org_id, "inventory", "low_stock")
    if not template:
        return []

    items = (
        _db()
        .table("inventory_items")
        .select("id, name, sku, current_stock, min_stock, reorder_point")
        .eq("org_id", org_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )

    started = []
    for item in items:
        current = float(item.get("current_stock") or 0)
        min_stock = float(item.get("min_stock") or 0)
        if current > min_stock:
            continue
        item_id = item["id"]
        if _pending_po_for_item(org_id, item_id):
            continue
        if _recent_run_exists(org_id, template["id"], item_id, days=7):
            continue
        run = start_event_run(
            org_id=org_id,
            module_key="inventory",
            event="low_stock",
            input_data={"item_id": item_id},
            name=f"Stock bajo: {item.get('name', 'Artículo')}",
            metadata={"item_id": item_id},
        )
        if run:
            started.append({"item_id": item_id, "run_id": run.get("id")})
    return started


def _recent_run_for_period(org_id: str, template_id: str, year: int, month: int) -> bool:
    try:
        rows = (
            _db()
            .table("workflow_runs")
            .select("id, metadata")
            .eq("org_id", org_id)
            .eq("template_id", template_id)
            .execute()
            .data
            or []
        )
        for row in rows:
            meta = row.get("metadata") or {}
            if meta.get("year") == year and meta.get("month") == month:
                return True
        return False
    except Exception as e:
        log.warning(f"[scheduler] Could not check period runs: {e}")
        return False


def tick_accounting(org_id: str) -> List[dict]:
    """Al final de mes, dispara cierre mensual contable."""
    if not _module_enabled(org_id, "accounting"):
        return []

    template = get_module_template_by_event(org_id, "accounting", "monthly_close")
    if not template:
        return []

    today = date.today()
    # Último día del mes
    if today.month == 12:
        last_day = today.replace(day=31)
    else:
        last_day = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    if today != last_day:
        return []

    if _recent_run_for_period(org_id, template["id"], today.year, today.month):
        return []

    run = start_event_run(
        org_id=org_id,
        module_key="accounting",
        event="monthly_close",
        input_data={},
        name=f"Cierre mensual contable - {today.strftime('%B %Y')}",
        metadata={"year": today.year, "month": today.month},
    )
    if run:
        return [{"run_id": run.get("id"), "year": today.year, "month": today.month}]
    return []


def _recent_run_for_week(org_id: str, template_id: str, iso_year: int, iso_week: int) -> bool:
    try:
        rows = (
            _db()
            .table("workflow_runs")
            .select("id, metadata")
            .eq("org_id", org_id)
            .eq("template_id", template_id)
            .execute()
            .data
            or []
        )
        for row in rows:
            meta = row.get("metadata") or {}
            if meta.get("iso_year") == iso_year and meta.get("iso_week") == iso_week:
                return True
        return False
    except Exception as e:
        log.warning(f"[scheduler] Could not check week runs: {e}")
        return False


def tick_control(org_id: str) -> List[dict]:
    """Revisión semanal de presupuesto + escaneo mensual de dependencia del founder."""
    if not _module_enabled(org_id, "control"):
        return []

    started = []

    budget_template = get_module_template_by_event(org_id, "control", "budget_weekly_review")
    if budget_template:
        iso_year, iso_week, _ = date.today().isocalendar()
        if not _recent_run_for_week(org_id, budget_template["id"], iso_year, iso_week):
            run = start_event_run(
                org_id=org_id,
                module_key="control",
                event="budget_weekly_review",
                input_data={},
                name=f"Revisión semanal de presupuesto - semana {iso_week}/{iso_year}",
                metadata={"iso_year": iso_year, "iso_week": iso_week},
            )
            if run:
                started.append({"run_id": run.get("id"), "event": "budget_weekly_review"})

    bottleneck_template = get_module_template_by_event(org_id, "control", "founder_bottleneck_detected")
    if bottleneck_template:
        today = date.today()
        if today.month == 12:
            last_day = today.replace(day=31)
        else:
            last_day = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        if today == last_day and not _recent_run_for_period(org_id, bottleneck_template["id"], today.year, today.month):
            run = start_event_run(
                org_id=org_id,
                module_key="control",
                event="founder_bottleneck_detected",
                input_data={},
                name=f"Detección de cuello de botella del founder - {today.strftime('%B %Y')}",
                metadata={"year": today.year, "month": today.month},
            )
            if run:
                started.append({"run_id": run.get("id"), "event": "founder_bottleneck_detected"})

    return started


def tick_cumple(org_id: str) -> List[dict]:
    """Revisión semanal de cumplimiento: calendario fiscal + auditoría de CFDI."""
    if not _module_enabled(org_id, "cumple"):
        return []

    template = get_module_template_by_event(org_id, "cumple", "cumple_weekly_check")
    if not template:
        return []

    iso_year, iso_week, _ = date.today().isocalendar()
    if _recent_run_for_week(org_id, template["id"], iso_year, iso_week):
        return []

    run = start_event_run(
        org_id=org_id,
        module_key="cumple",
        event="cumple_weekly_check",
        input_data={},
        name=f"Revisión semanal de cumplimiento - semana {iso_week}/{iso_year}",
        metadata={"iso_year": iso_year, "iso_week": iso_week},
    )
    if run:
        return [{"run_id": run.get("id"), "event": "cumple_weekly_check"}]
    return []


def _ai_staff_enabled(org_id: str, staff_key: str) -> bool:
    try:
        row = (
            _db()
            .table("organization_ai_staff")
            .select("enabled")
            .eq("organization_id", org_id)
            .eq("staff_key", staff_key)
            .maybe_single()
            .execute()
            .data
        )
        return bool(row and row.get("enabled"))
    except Exception as e:
        log.warning(f"[scheduler] Could not check ai staff {staff_key}: {e}")
        return False


def _recent_run_today(org_id: str, template_id: str) -> bool:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        rows = (
            _db()
            .table("workflow_runs")
            .select("id, metadata")
            .eq("org_id", org_id)
            .eq("template_id", template_id)
            .gte("started_at", today)
            .execute()
            .data
            or []
        )
        return len(rows) > 0
    except Exception as e:
        log.warning(f"[scheduler] Could not check today's runs: {e}")
        return False


def tick_chief_of_staff(org_id: str, event: Optional[str] = None) -> List[dict]:
    """
    Dispara la orquestación diaria del Chief of Staff.
    Por defecto intenta el template de orquestación; si no existe, cae al resumen diario.
    """
    if not _ai_staff_enabled(org_id, "chief_of_staff"):
        return []

    events = [event] if event else ["orchestrate_daily", "daily_summary"]
    for ev in events:
        template = get_module_template_by_event(org_id, "chief_of_staff", ev)
        if not template:
            continue
        if _recent_run_today(org_id, template["id"]):
            return []
        run = start_event_run(
            org_id=org_id,
            module_key="chief_of_staff",
            event=ev,
            input_data={"date": date.today().isoformat()},
            name=f"Orquestación diaria - {date.today().isoformat()}" if ev == "orchestrate_daily" else f"Resumen ejecutivo diario - {date.today().isoformat()}",
            metadata={"date": date.today().isoformat(), "event": ev},
        )
        if run:
            return [{"run_id": run.get("id"), "date": date.today().isoformat(), "event": ev}]
    return []


def process_agent_command(org_id: str, command_msg: dict) -> dict:
    """
    Ejecuta un comando pendiente de un agente (módulo o AI Staff).
    Usa el scheduler del módulo correspondiente o el runner de AI Staff.
    """
    metadata = command_msg.get("metadata") or {}
    agent_key = metadata.get("agent_key")
    command = metadata.get("command")
    message_id = command_msg.get("id")

    if not agent_key or not command:
        mark_command_processed(message_id, "failed", error="missing agent_key or command")
        return {"ok": False, "error": "missing agent_key or command"}

    # AI Staff (sales_agent, collector_agent)
    if is_ai_staff_key(agent_key):
        if not _ai_staff_enabled(org_id, agent_key):
            mark_command_processed(message_id, "skipped", result={"reason": "ai_staff not enabled"})
            return {"ok": True, "status": "skipped", "agent_key": agent_key, "command": command}

        mark_command_processed(message_id, "running")
        try:
            result = run_ai_staff_command(org_id, agent_key, command)
            mark_command_processed(message_id, "completed", result=result)
            return {
                "ok": True,
                "agent_key": agent_key,
                "command": command,
                "started": result,
                "started_count": result.get("actions_created", 0),
            }
        except Exception as e:
            log.error(f"[scheduler] Error processing AI Staff command {agent_key}/{command}: {e}", exc_info=True)
            mark_command_processed(message_id, "failed", error=str(e))
            return {"ok": False, "agent_key": agent_key, "command": command, "error": str(e)}

    # Módulos de negocio (collections, inventory, crm, accounting)
    if not _module_enabled(org_id, agent_key):
        mark_command_processed(message_id, "skipped", result={"reason": "module not enabled"})
        return {"ok": True, "status": "skipped", "agent_key": agent_key, "command": command}

    tick_fn = _module_tick_fn(agent_key)
    if not tick_fn:
        mark_command_processed(message_id, "failed", error=f"no tick function for {agent_key}")
        return {"ok": False, "error": f"no tick function for {agent_key}"}

    mark_command_processed(message_id, "running")
    try:
        started = tick_fn(org_id)
        mark_command_processed(message_id, "completed", result={"started": started})
        return {
            "ok": True,
            "agent_key": agent_key,
            "command": command,
            "started": started,
            "started_count": len(started),
        }
    except Exception as e:
        log.error(f"[scheduler] Error processing command {agent_key}/{command}: {e}", exc_info=True)
        mark_command_processed(message_id, "failed", error=str(e))
        return {"ok": False, "agent_key": agent_key, "command": command, "error": str(e)}


def process_all_pending_commands(org_id: str) -> List[dict]:
    """Procesa todos los comandos pendientes para todos los agentes y AI Staff."""
    processed = []
    for agent_key in list_agent_keys():
        pending = get_pending_commands(org_id, agent_key=agent_key)
        for cmd in pending:
            processed.append(process_agent_command(org_id, cmd))
    return processed


def process_pending_commands(org_id: str, agent_key: Optional[str] = None) -> List[dict]:
    """Procesa comandos pendientes, opcionalmente filtrados por agente."""
    processed = []
    pending = get_pending_commands(org_id, agent_key=agent_key)
    for cmd in pending:
        processed.append(process_agent_command(org_id, cmd))
    return processed


def tick_org(org_id: str, module_key: Optional[str] = None) -> dict:
    """
    Ejecuta un tick completo de workflows programados/eventos para una org.
    Cuando se llama sin module_key, el Chief of Staff orquesta primero,
    espera a que termine, procesa los comandos emitidos y luego ejecuta
    los ticks normales de cada módulo (que se saltarán duplicados).
    """
    result = {"total_started": 0}

    # Tick específico de un solo módulo
    if module_key is not None:
        if module_key == "chief_of_staff":
            chief = tick_chief_of_staff(org_id)
            result["chief_of_staff_runs"] = chief
            result["total_started"] += len(chief)
            return result

        processed = process_pending_commands(org_id, agent_key=module_key)
        result["processed_commands"] = processed
        result["total_started"] += sum(p.get("started_count", 0) for p in processed if p.get("ok"))

        tick_fn = _module_tick_fn(module_key)
        if tick_fn:
            started = tick_fn(org_id)
            result[f"{module_key}_runs"] = started
            result["total_started"] += len(started)
        return result

    # Tick completo con orquestación
    chief = tick_chief_of_staff(org_id)
    result["chief_of_staff_runs"] = chief
    result["total_started"] += len(chief)

    # Esperar a que el Chief of Staff termine de publicar comandos
    for run_info in chief:
        run_id = run_info.get("run_id")
        if run_id:
            _wait_for_run(run_id, timeout=15.0)

    # Procesar comandos emitidos por el Chief of Staff
    processed = process_all_pending_commands(org_id)
    result["processed_commands"] = processed
    result["total_started"] += sum(p.get("started_count", 0) for p in processed if p.get("ok"))

    # Ticks normales de cada módulo (evitan duplicados con los comandos ya ejecutados)
    for m in ["collections", "crm", "inventory", "accounting", "control", "cumple"]:
        tick_fn = _module_tick_fn(m)
        if not tick_fn:
            continue
        started = tick_fn(org_id)
        result[f"{m}_runs"] = started
        result["total_started"] += len(started)

    # Revisar alertas proactivas del Chief of Staff
    try:
        result["alerts"] = check_and_send_alerts(org_id)
    except Exception as e:
        log.error(f"[scheduler] Alert check failed for org {org_id}: {e}", exc_info=True)
        result["alerts"] = {"error": str(e)}

    return result


def tick_all() -> List[dict]:
    """Tick para todas las organizaciones que tengan módulos activos."""
    rows = (
        _db()
        .table("organization_modules")
        .select("organization_id")
        .eq("enabled", True)
        .execute()
        .data
        or []
    )
    org_ids = list({r["organization_id"] for r in rows})
    results = []
    for org_id in org_ids:
        try:
            result = tick_org(org_id)
            result["org_id"] = org_id
            results.append(result)
        except Exception as e:
            log.error(f"[scheduler] Tick failed for org {org_id}: {e}", exc_info=True)
            results.append({"org_id": org_id, "error": str(e)})
    return results


# ── Helpers reactivos para rutas de la API ────────────────────────────────────

MODULE_TICK_FNS = {
    "collections": tick_collections,
    "crm": tick_crm,
    "inventory": tick_inventory,
    "accounting": tick_accounting,
    "control": tick_control,
    "cumple": tick_cumple,
}


def schedule_module_tick(org_id: str, module_key: str, background_tasks=None):
    """
    Ejecuta un tick del módulo correspondiente. Si se pasa `background_tasks`,
    se programa para correr después de responder al cliente (non-blocking).
    """
    tick_fn = MODULE_TICK_FNS.get(module_key)
    if not tick_fn:
        log.warning(f"[scheduler] No tick function for module {module_key}")
        return
    if background_tasks is not None:
        background_tasks.add_task(tick_fn, org_id)
        log.info(f"[scheduler] Scheduled {module_key} tick for org {org_id}")
    else:
        try:
            tick_fn(org_id)
        except Exception as e:
            log.error(f"[scheduler] {module_key} tick failed for org {org_id}: {e}", exc_info=True)
