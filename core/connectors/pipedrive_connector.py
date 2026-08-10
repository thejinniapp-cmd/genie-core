"""core/connectors/pipedrive_connector.py — Pipedrive connector"""
import httpx
from core.connectors.executor import BaseConnector, register_connector

BASE = "https://api.pipedrive.com/v1"


@register_connector("pipedrive")
class PipedriveConnector(BaseConnector):
    def _params(self) -> dict:
        return {"api_token": self.credentials["api_token"]}

    def list_contacts(self, limit: int = 10) -> list:
        r = httpx.get(f"{BASE}/persons", params={**self._params(), "limit": limit}, timeout=15)
        r.raise_for_status()
        return r.json().get("data") or []

    def list_deals(self, limit: int = 10) -> list:
        r = httpx.get(f"{BASE}/deals", params={**self._params(), "limit": limit}, timeout=15)
        r.raise_for_status()
        return r.json().get("data") or []

    def list_products(self, limit: int = 10) -> list:
        r = httpx.get(f"{BASE}/products", params={**self._params(), "limit": limit}, timeout=15)
        r.raise_for_status()
        return r.json().get("data") or []

    def sync(self) -> dict:
        """Sincroniza personas, empresas, deals y productos hacia Genie."""
        from core.sync.engine import run_sync
        return run_sync(self.org_id, "pipedrive", self.credentials)
