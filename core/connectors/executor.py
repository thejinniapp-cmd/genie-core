"""
core/connectors/executor.py
===========================
Despacha acciones a los conectores configurados.

Cuando un agente quiere ejecutar una acción (ej: enviar un email),
llama a execute_connector_action() y este módulo:
1. Verifica que el conector esté activo para el org
2. Obtiene las credenciales del tenant
3. Despacha a la implementación del conector
4. Devuelve el resultado

Nuevos conectores se agregan implementando la interfaz BaseConnector
y registrándolos en CONNECTOR_REGISTRY.
"""

import logging
from typing import Any
from core import tenant

log = logging.getLogger("genie.connectors")


# ── Registro de conectores ────────────────────────────────────────────────────
# Cada entrada: "connector_type" → clase del conector

CONNECTOR_REGISTRY: dict = {}


def register_connector(connector_type: str):
    """Decorador para registrar un conector."""
    def decorator(cls):
        CONNECTOR_REGISTRY[connector_type] = cls
        log.info(f"[connectors] Registered: {connector_type}")
        return cls
    return decorator


# ── Ejecutor principal ────────────────────────────────────────────────────────

def execute_connector_action(
    org_id: str,
    connector_type: str,
    action: str,
    params: dict,
) -> Any:
    """
    Ejecuta una acción en un conector para una organización.

    Uso:
        result = execute_connector_action(
            org_id, "gmail", "send_email",
            {"to": "...", "subject": "...", "body": "..."}
        )
    """
    if connector_type not in CONNECTOR_REGISTRY:
        raise ValueError(f"Connector '{connector_type}' not registered. Available: {list(CONNECTOR_REGISTRY.keys())}")

    # Verificar que el conector está activo
    is_active = tenant.connector_is_active(org_id, connector_type)
    if not is_active:
        raise ValueError(f"Connector '{connector_type}' is not configured for this organization")

    # Obtener credenciales
    credentials = tenant.get_credentials(org_id, connector_type)

    # Instanciar y ejecutar
    connector_cls = CONNECTOR_REGISTRY[connector_type]
    connector = connector_cls(credentials)

    if not hasattr(connector, action):
        raise ValueError(f"Action '{action}' not found in connector '{connector_type}'")

    log.info(f"[connectors] {connector_type}.{action} for org {org_id}")
    return getattr(connector, action)(**params)


# ── Clase base para conectores ────────────────────────────────────────────────

class BaseConnector:
    """
    Clase base para todos los conectores.

    Cada conector implementa sus acciones como métodos.
    Las credenciales se inyectan en __init__.

    Ejemplo:
        @register_connector("gmail")
        class GmailConnector(BaseConnector):
            def send_email(self, to, subject, body):
                # usar self.credentials["access_token"]
                ...
    """

    def __init__(self, credentials: dict):
        self.credentials = credentials

    def get_tools_schema(self) -> list[dict]:
        """
        Devuelve el schema de tools para el modelo de IA.
        Sobreescribir en cada conector.
        """
        return []


# ── Conectores built-in ───────────────────────────────────────────────────────
# Se importan aquí para que se registren automáticamente al importar este módulo

def _load_builtin_connectors():
    try:
        from core.connectors import gmail, google_drive, telegram_connector, whatsapp_connector
    except ImportError as e:
        log.debug(f"[connectors] Some built-in connectors not loaded: {e}")

_load_builtin_connectors()
