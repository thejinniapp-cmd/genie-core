"""
agents/base_agent.py
====================
Clase base para todos los agentes de Genie.

Cada agente (prompt-based o worker Python) hereda de BaseAgent
y obtiene automáticamente:
- Polling de jobs desde Supabase
- Logging de estado en tiempo real
- Audit log automático
- Acceso a tenant (credenciales, config)
- Reintentos con backoff
- Human-in-the-loop (escalación al usuario cuando se requiere)
"""

import os
import time
import logging
import traceback
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from supabase import create_client

from core import tenant, audit

log = logging.getLogger("genie.agent")

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", 10))


class BaseAgent(ABC):
    """
    Clase base para todos los agentes de Genie.

    Para crear un agente nuevo:

        class MiAgente(BaseAgent):
            agent_id = "mi_agente"
            job_type = "mi_tipo_de_job"

            async def run(self, job: dict, org_id: str) -> dict:
                # lógica del agente
                return {"resultado": "..."}
    """

    agent_id: str = "base"
    job_type: str = "generic"
    max_retries: int = 3

    def __init__(self):
        self.supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )

    # ── Loop principal ────────────────────────────────────────────────────────

    def main(self):
        """Punto de entrada. Corre el loop de polling indefinidamente."""
        log.info(f"[{self.agent_id}] Starting — polling every {POLL_INTERVAL}s")
        while True:
            try:
                self._poll_and_process()
            except Exception as e:
                log.error(f"[{self.agent_id}] Poll error: {e}")
            time.sleep(POLL_INTERVAL)

    def _poll_and_process(self):
        """Busca jobs pendientes y los procesa."""
        jobs = self._fetch_pending_jobs()
        if not jobs:
            return
        log.info(f"[{self.agent_id}] Found {len(jobs)} pending job(s)")
        for job in jobs:
            self._process_job(job)

    def _fetch_pending_jobs(self) -> list[dict]:
        """Busca jobs pendientes para este tipo de agente."""
        res = (
            self.supabase
            .table("jobs")
            .select("*")
            .eq("agent_type", self.job_type)
            .eq("status", "pending")
            .order("created_at")
            .limit(5)
            .execute()
        )
        return res.data or []

    def _process_job(self, job: dict):
        """Procesa un job con manejo de errores y reintentos."""
        job_id = job["id"]
        org_id = job["org_id"]
        attempt = job.get("attempt", 1)

        self._set_job_status(job_id, "running", started_at=datetime.now(timezone.utc).isoformat())
        self._add_log(job_id, "start", f"Agent {self.agent_id} started (attempt {attempt})")

        audit.log_event(
            org_id=org_id,
            action=f"{self.agent_id}.started",
            actor_type="agent",
            actor_id=self.agent_id,
            job_id=job_id,
            stream_id=job.get("stream_id"),
            input_data=job.get("input_data"),
            status="pending",
        )

        try:
            result = self.run(job, org_id)

            self._set_job_status(
                job_id, "completed",
                output=result,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            self._add_log(job_id, "complete", "Job completed successfully")

            audit.log_event(
                org_id=org_id,
                action=f"{self.agent_id}.completed",
                actor_type="agent",
                actor_id=self.agent_id,
                job_id=job_id,
                stream_id=job.get("stream_id"),
                input_data=job.get("input_data"),
                output_data=result,
                status="ok",
            )

            self.on_success(job, result)

        except HumanReviewRequired as e:
            # El agente necesita aprobación humana antes de continuar
            self._set_job_status(job_id, "waiting_approval", error=str(e))
            self._add_log(job_id, "human_review", str(e))
            self._notify_approval_required(job, str(e))

            audit.log_event(
                org_id=org_id,
                action=f"{self.agent_id}.waiting_approval",
                actor_type="agent",
                actor_id=self.agent_id,
                job_id=job_id,
                stream_id=job.get("stream_id"),
                metadata={"reason": str(e)},
                status="pending",
            )

        except Exception as e:
            tb = traceback.format_exc()
            log.error(f"[{self.agent_id}] Job {job_id} failed: {e}\n{tb}")

            if attempt < self.max_retries:
                self._set_job_status(job_id, "pending", error=str(e), attempt=attempt + 1)
                self._add_log(job_id, "retry", f"Will retry (attempt {attempt + 1}/{self.max_retries})")
            else:
                self._set_job_status(job_id, "failed", error=str(e),
                                     finished_at=datetime.now(timezone.utc).isoformat())
                self._add_log(job_id, "failed", f"Failed after {self.max_retries} attempts: {str(e)[:200]}")

            audit.log_event(
                org_id=org_id,
                action=f"{self.agent_id}.failed",
                actor_type="agent",
                actor_id=self.agent_id,
                job_id=job_id,
                stream_id=job.get("stream_id"),
                metadata={"error": str(e), "attempt": attempt},
                status="error",
            )

            self.on_error(job, e)

    # ── Métodos que los agentes pueden sobreescribir ──────────────────────────

    @abstractmethod
    def run(self, job: dict, org_id: str) -> dict:
        """
        Lógica principal del agente.
        Recibe el job y el org_id.
        Debe devolver un dict con el resultado.
        Lanzar HumanReviewRequired si necesita aprobación.
        """
        ...

    def on_success(self, job: dict, result: dict):
        """Hook llamado al completar un job exitosamente. Sobreescribir si se necesita."""
        pass

    def on_error(self, job: dict, error: Exception):
        """Hook llamado al fallar un job. Sobreescribir si se necesita."""
        pass

    # ── Helpers de estado ─────────────────────────────────────────────────────

    def _set_job_status(self, job_id: str, status: str, **kwargs):
        update = {"status": status, **kwargs}
        self.supabase.table("jobs").update(update).eq("id", job_id).execute()

    def _add_log(self, job_id: str, step: str, message: str):
        try:
            job = self.supabase.table("jobs").select("logs").eq("id", job_id).single().execute()
            logs = job.data.get("logs") or []
            logs.append({
                "step": step,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            self.supabase.table("jobs").update({"logs": logs}).eq("id", job_id).execute()
        except Exception as e:
            log.warning(f"[{self.agent_id}] Could not add log: {e}")

    def _notify_approval_required(self, job: dict, reason: str):
        """Crea una notificación en el stream para que el usuario apruebe."""
        try:
            self.supabase.table("stream_notifications").insert({
                "org_id": job["org_id"],
                "stream_id": job.get("stream_id"),
                "job_id": job["id"],
                "type": "approval_required",
                "message": reason,
                "status": "pending",
            }).execute()
        except Exception as e:
            log.warning(f"[{self.agent_id}] Could not create approval notification: {e}")

    # ── Enqueue helper ────────────────────────────────────────────────────────

    def enqueue(
        self,
        org_id: str,
        job_type: str,
        input_data: dict,
        stream_id: str | None = None,
        priority: int = 0,
    ) -> str:
        """Encola un nuevo job para otro agente."""
        res = self.supabase.table("jobs").insert({
            "org_id": org_id,
            "stream_id": stream_id,
            "agent_type": job_type,
            "status": "pending",
            "input_data": input_data,
            "priority": priority,
            "attempt": 1,
        }).execute()
        job_id = res.data[0]["id"] if res.data else None
        log.info(f"[{self.agent_id}] Enqueued job {job_type} → {job_id}")
        return job_id


# ── Excepción especial para human-in-the-loop ────────────────────────────────

class HumanReviewRequired(Exception):
    """
    Lanzar esta excepción cuando el agente necesita aprobación humana.

    El job queda en estado 'waiting_approval' y se notifica al usuario.
    Cuando el usuario aprueba, el job se reanuda.

    Ejemplo:
        if precio > limite_autonomia:
            raise HumanReviewRequired(
                f"El precio ${precio:,.0f} supera el límite de autonomía ${limite:,.0f}. "
                f"¿Aprobas publicar con opción #{opcion_rank}?"
            )
    """
    pass
