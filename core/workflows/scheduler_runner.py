"""core/workflows/scheduler_runner.py — Scheduler periódico para el Chief of Staff.

Usa APScheduler para ejecutar automáticamente el tick de orquestación del
Chief of Staff sin depender de que un usuario presione un botón.
"""
import os
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.workflows.scheduler import tick_all

log = logging.getLogger("genie.scheduler_runner")

scheduler: BackgroundScheduler | None = None


def _chief_tick() -> None:
    """Ejecuta el tick completo de Chief of Staff y módulos."""
    try:
        log.info("[scheduler] Running periodic Chief of Staff tick_all")
        tick_all()
    except Exception as e:
        log.error(f"[scheduler] tick_all failed: {e}", exc_info=True)


def start_scheduler() -> None:
    """Arranca el scheduler con el job periódico del Chief of Staff."""
    global scheduler
    if scheduler is not None:
        log.warning("[scheduler] Already started, skipping")
        return

    interval_minutes = int(os.environ.get("CHIEF_OF_STAFF_TICK_MINUTES", "15"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _chief_tick,
        IntervalTrigger(minutes=interval_minutes),
        id="chief_of_staff_tick_all",
        name="Chief of Staff periodic tick",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    log.info(f"[scheduler] Started Chief of Staff tick every {interval_minutes} minutes")


def stop_scheduler() -> None:
    """Detiene el scheduler."""
    global scheduler
    if scheduler is None:
        return
    try:
        scheduler.shutdown()
        log.info("[scheduler] Scheduler shut down")
    except Exception as e:
        log.warning(f"[scheduler] Error shutting down scheduler: {e}")
    finally:
        scheduler = None
