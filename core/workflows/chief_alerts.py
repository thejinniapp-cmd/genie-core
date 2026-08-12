"""core/workflows/chief_alerts.py — Alertas proactivas del Chief of Staff.

El Chief revisa métricas clave del negocio y, si cruzan umbrales configurados,
genera notificaciones in-app y (opcionalmente) envía mensajes por los
conectores activos (Gmail, Slack, Telegram).
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from supabase import create_client

from core.workflows.actions import get_business_metrics

log = logging.getLogger("genie.chief_alerts")


# Métrica → (umbral por defecto, alerta si es menor que el umbral)
DEFAULT_ALERTS = {
    "overdue": {"threshold": 0, "enabled": True, "is_lower_bound": False},
    "low_stock": {"threshold": 0, "enabled": True, "is_lower_bound": False},
    "outstanding": {"threshold": 100000, "enabled": True, "is_lower_bound": False},
    "monthly_net": {"threshold": 0, "enabled": True, "is_lower_bound": True},
    "budget_variance_pct": {"threshold": 20, "enabled": True, "is_lower_bound": False},
    "founder_dependency_pct": {"threshold": 50, "enabled": True, "is_lower_bound": False},
    "runway_weeks": {"threshold": 4, "enabled": True, "is_lower_bound": True},
    "customer_concentration_pct": {"threshold": 30, "enabled": True, "is_lower_bound": False},
    "fiscal_obligations_due_soon": {"threshold": 0, "enabled": True, "is_lower_bound": False},
    "cfdi_audit_findings_count": {"threshold": 0, "enabled": True, "is_lower_bound": False},
}


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _cooldown_hours() -> int:
    try:
        return int(os.environ.get("CHIEF_ALERT_COOLDOWN_HOURS", "4"))
    except ValueError:
        return 4


def get_alert_settings(org_id: str) -> List[Dict[str, Any]]:
    """Devuelve la configuración de alertas para la org, creando defaults si no existen."""
    try:
        rows = (
            _db()
            .table("chief_alert_settings")
            .select("*")
            .eq("org_id", org_id)
            .execute()
            .data
            or []
        )
        existing = {r["metric_key"]: r for r in rows}
        missing = [k for k in DEFAULT_ALERTS if k not in existing]
        for metric_key in missing:
            default = DEFAULT_ALERTS[metric_key]
            try:
                res = (
                    _db()
                    .table("chief_alert_settings")
                    .insert({
                        "org_id": org_id,
                        "metric_key": metric_key,
                        "threshold": default["threshold"],
                        "enabled": default["enabled"],
                        "is_lower_bound": default["is_lower_bound"],
                    })
                    .execute()
                    .data
                )
                if res:
                    existing[metric_key] = res[0]
            except Exception as e:
                log.warning(f"[chief_alerts] Could not insert default setting {metric_key}: {e}")
        return list(existing.values())
    except Exception as e:
        log.warning(f"[chief_alerts] Could not load alert settings: {e}")
        return []


def upsert_alert_settings(org_id: str, updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Actualiza umbral/habilitación de alertas para la org."""
    results = []
    for u in updates:
        metric_key = u.get("metric_key")
        if not metric_key or metric_key not in DEFAULT_ALERTS:
            continue
        try:
            res = (
                _db()
                .table("chief_alert_settings")
                .upsert({
                    "org_id": org_id,
                    "metric_key": metric_key,
                    "threshold": u.get("threshold", DEFAULT_ALERTS[metric_key]["threshold"]),
                    "enabled": u.get("enabled", True),
                    "is_lower_bound": u.get("is_lower_bound", DEFAULT_ALERTS[metric_key]["is_lower_bound"]),
                    "channels": u.get("channels", []),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }, on_conflict="org_id,metric_key")
                .execute()
                .data
            )
            if res:
                results.append(res[0])
        except Exception as e:
            log.warning(f"[chief_alerts] Could not upsert setting {metric_key}: {e}")
    return results


def _get_state(org_id: str, metric_key: str) -> Optional[Dict[str, Any]]:
    try:
        return (
            _db()
            .table("chief_alert_state")
            .select("*")
            .eq("org_id", org_id)
            .eq("metric_key", metric_key)
            .maybe_single()
            .execute()
            .data
        )
    except Exception as e:
        log.warning(f"[chief_alerts] Could not read state {metric_key}: {e}")
        return None


def _update_state(org_id: str, metric_key: str, value: float):
    try:
        _db().table("chief_alert_state").upsert({
            "org_id": org_id,
            "metric_key": metric_key,
            "last_value": value,
            "last_alert_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="org_id,metric_key").execute()
    except Exception as e:
        log.warning(f"[chief_alerts] Could not update state {metric_key}: {e}")


def _create_in_app_notification(org_id: str, title: str, body: str, metric_key: str, value: float):
    try:
        _db().table("stream_notifications").insert({
            "org_id": org_id,
            "type": "alert",
            "message": f"{title}\n{body}",
            "status": "pending",
            "metadata": {
                "title": title,
                "body": body,
                "source": "chief_of_staff",
                "metric_key": metric_key,
                "value": value,
            },
        }).execute()
    except Exception as e:
        log.warning(f"[chief_alerts] Could not create in-app notification: {e}")


def _active_connectors(org_id: str) -> List[str]:
    try:
        rows = (
            _db()
            .table("connectors")
            .select("connector_type")
            .eq("org_id", org_id)
            .eq("status", "connected")
            .execute()
            .data
            or []
        )
        return [r["connector_type"] for r in rows]
    except Exception as e:
        log.warning(f"[chief_alerts] Could not list connectors: {e}")
        return []


def _send_alert(org_id: str, title: str, body: str, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Envía la alerta por todos los canales configurados y conectores activos."""
    sent = []
    active = set(_active_connectors(org_id))
    channels = settings.get("channels") or []
    # Canales configurados por la org; si no hay, se usan variables de entorno como fallback.
    for ch in channels:
        ch_type = ch.get("type")
        if ch_type not in active and ch_type not in ("gmail", "slack", "telegram"):
            continue
        try:
            if ch_type == "gmail":
                _send_email_alert(org_id, ch.get("to"), title, body)
            elif ch_type == "slack":
                _send_slack_alert(org_id, ch.get("channel"), title, body)
            elif ch_type == "telegram":
                _send_telegram_alert(org_id, ch.get("chat_id"), title, body)
            sent.append({"channel": ch_type, "ok": True})
        except Exception as e:
            log.warning(f"[chief_alerts] Channel {ch_type} failed: {e}")
            sent.append({"channel": ch_type, "ok": False, "error": str(e)})

    # Fallback por entorno si no hay canales configurados
    if not channels:
        for ch_type in active:
            try:
                if ch_type == "gmail":
                    _send_email_alert(org_id, None, title, body)
                elif ch_type == "slack":
                    _send_slack_alert(org_id, None, title, body)
                elif ch_type == "telegram":
                    _send_telegram_alert(org_id, None, title, body)
                sent.append({"channel": ch_type, "ok": True})
            except Exception as e:
                log.warning(f"[chief_alerts] Fallback channel {ch_type} failed: {e}")
                sent.append({"channel": ch_type, "ok": False, "error": str(e)})
    return sent


def _send_email_alert(org_id: str, to: Optional[str], subject: str, body: str):
    to = to or os.environ.get("CHIEF_ALERT_EMAIL")
    if not to:
        raise ValueError("No destination email configured")
    from core.connectors.executor import execute_connector_action
    execute_connector_action(
        org_id=org_id,
        connector_type="gmail",
        action="send_email",
        params={"to": to, "subject": subject, "body": body, "html": False},
    )


def _send_slack_alert(org_id: str, channel: Optional[str], title: str, body: str):
    channel = channel or os.environ.get("CHIEF_ALERT_SLACK_CHANNEL")
    if not channel:
        raise ValueError("No Slack channel configured")
    from core.connectors.executor import execute_connector_action
    execute_connector_action(
        org_id=org_id,
        connector_type="slack",
        action="send_message",
        params={"channel": channel, "text": f"*{title}*\n{body}"},
    )


def _send_telegram_alert(org_id: str, chat_id: Optional[str], title: str, body: str):
    chat_id = chat_id or os.environ.get("CHIEF_ALERT_TELEGRAM_CHAT_ID")
    if not chat_id:
        raise ValueError("No Telegram chat_id configured")
    from core.connectors.executor import execute_connector_action
    execute_connector_action(
        org_id=org_id,
        connector_type="telegram",
        action="send_message",
        params={"chat_id": chat_id, "text": f"*{title}*\n{body}"},
    )


def _metric_label(metric_key: str) -> str:
    return {
        "overdue": "Facturas vencidas",
        "low_stock": "Stock bajo",
        "outstanding": "Cuentas por cobrar",
        "monthly_net": "Neto mensual",
        "budget_variance_pct": "Desviación de presupuesto",
        "founder_dependency_pct": "Dependencia del founder",
        "runway_weeks": "Runway de caja",
        "customer_concentration_pct": "Concentración de clientes",
        "fiscal_obligations_due_soon": "Obligaciones fiscales por vencer",
        "cfdi_audit_findings_count": "Hallazgos de auditoría CFDI",
    }.get(metric_key, metric_key)


def _format_value(metric_key: str, value: float) -> str:
    if metric_key in ("overdue", "outstanding", "monthly_net"):
        return f"${value:,.2f}"
    if metric_key in ("budget_variance_pct", "founder_dependency_pct", "customer_concentration_pct"):
        return f"{value:.1f}%"
    if metric_key == "runway_weeks":
        return f"{value:.1f} semanas"
    return f"{int(value)}"


def _should_alert(value: float, threshold: float, is_lower_bound: bool) -> bool:
    if is_lower_bound:
        return value < threshold
    return value > threshold


def check_and_send_alerts(org_id: str) -> Dict[str, Any]:
    """Revisa métricas, genera alertas y las envía por los canales configurados."""
    settings = get_alert_settings(org_id)
    metrics = get_business_metrics(org_id)
    cooldown = timedelta(hours=_cooldown_hours())
    now = datetime.now(timezone.utc)
    alerts = []

    for setting in settings:
        metric_key = setting.get("metric_key")
        if not metric_key or not setting.get("enabled"):
            continue
        if metric_key not in metrics:
            continue
        value = float(metrics.get(metric_key) or 0)
        threshold = float(setting.get("threshold") or 0)
        is_lower_bound = bool(setting.get("is_lower_bound"))

        if not _should_alert(value, threshold, is_lower_bound):
            continue

        state = _get_state(org_id, metric_key)
        if state and state.get("last_alert_at"):
            last_alert = datetime.fromisoformat(state["last_alert_at"].replace("Z", "+00:00"))
            if now - last_alert < cooldown:
                continue

        label = _metric_label(metric_key)
        title = f"Alerta de Chief of Staff: {label}"
        body = f"{label} está en {_format_value(metric_key, value)}."
        if is_lower_bound:
            body += f" Umbral mínimo: {_format_value(metric_key, threshold)}."
        else:
            body += f" Umbral máximo: {_format_value(metric_key, threshold)}."

        _create_in_app_notification(org_id, title, body, metric_key, value)
        sent = _send_alert(org_id, title, body, setting)
        _update_state(org_id, metric_key, value)

        alerts.append({
            "metric_key": metric_key,
            "value": value,
            "threshold": threshold,
            "sent": sent,
        })
        log.info(f"[chief_alerts] Alert sent for {metric_key}={value} org={org_id}")

    return {"alerts": alerts, "metrics": metrics}
