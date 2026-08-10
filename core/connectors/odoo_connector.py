"""core/connectors/odoo_connector.py — Odoo connector"""
import httpx
from core.connectors.executor import BaseConnector, register_connector


@register_connector("odoo")
class OdooConnector(BaseConnector):
    def _call(self, model: str, method: str, args: list = None, kwargs: dict = None) -> any:
        url = self.credentials.get("url", "").rstrip("/")
        db = self.credentials.get("database")
        username = self.credentials.get("username")
        password = self.credentials.get("api_key")
        if not all([url, db, username, password]):
            raise ValueError("Faltan credenciales de Odoo")

        if not hasattr(self, "_uid"):
            auth = httpx.post(
                f"{url}/jsonrpc",
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {
                        "service": "common",
                        "method": "authenticate",
                        "args": [db, username, password, {}],
                    },
                    "id": 1,
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
                follow_redirects=False,
            )
            auth.raise_for_status()
            data = auth.json()
            if data.get("error"):
                raise ValueError(data["error"].get("message", "Error Odoo"))
            self._uid = data["result"]

        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [db, self._uid, password, model, method, args or [], kwargs or {}],
            },
            "id": 1,
        }
        r = httpx.post(f"{url}/jsonrpc", json=payload, headers={"Content-Type": "application/json"}, timeout=30, follow_redirects=False)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise ValueError(data["error"].get("message", "Error Odoo"))
        return data["result"]

    def list_partners(self, limit: int = 10) -> list:
        return self._call("res.partner", "search_read", kwargs={"domain": [("customer_rank", ">", 0)], "fields": ["id", "name", "is_company", "email"], "limit": limit})

    def list_products(self, limit: int = 10) -> list:
        return self._call("product.product", "search_read", kwargs={"domain": [("type", "in", ["product", "consu"])], "fields": ["id", "name", "default_code", "list_price", "qty_available"], "limit": limit})

    def list_invoices(self, limit: int = 10) -> list:
        return self._call("account.move", "search_read", kwargs={"domain": [("move_type", "=", "out_invoice")], "fields": ["id", "name", "partner_id", "amount_total", "state"], "limit": limit})

    def sync(self) -> dict:
        """Sincroniza partners, productos y facturas de Odoo hacia Genie."""
        from core.sync.engine import run_sync
        return run_sync(self.org_id, "odoo", self.credentials)
