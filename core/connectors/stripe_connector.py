"""core/connectors/stripe_connector.py — Stripe connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.stripe")
BASE = "https://api.stripe.com/v1"

@register_connector("stripe")
class StripeConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['secret_key']}"}

    def list_customers(self, limit: int = 10, email: str = None) -> list:
        """Lista clientes de Stripe."""
        params = {"limit": limit}
        if email:
            params["email"] = email
        r = httpx.get(f"{BASE}/customers", headers=self._h(), params=params, timeout=15)
        r.raise_for_status()
        customers = r.json().get("data", [])
        return [{"id": c["id"], "email": c.get("email"), "name": c.get("name"),
                 "created": c.get("created")} for c in customers]

    def list_payments(self, limit: int = 10) -> list:
        """Lista los pagos recientes."""
        r = httpx.get(f"{BASE}/payment_intents", headers=self._h(),
            params={"limit": limit}, timeout=15)
        r.raise_for_status()
        payments = r.json().get("data", [])
        return [{"id": p["id"], "amount": p["amount"] / 100, "currency": p["currency"],
                 "status": p["status"], "customer": p.get("customer")} for p in payments]

    def get_balance(self) -> dict:
        """Obtiene el balance actual de la cuenta Stripe."""
        r = httpx.get(f"{BASE}/balance", headers=self._h(), timeout=15)
        r.raise_for_status()
        data = r.json()
        available = data.get("available", [])
        return {"available": [{"amount": a["amount"] / 100, "currency": a["currency"]}
                               for a in available]}

    def create_payment_link(self, price_id: str, quantity: int = 1) -> dict:
        """Crea un link de pago."""
        r = httpx.post(f"{BASE}/payment_links", headers=self._h(), timeout=15,
            data={"line_items[0][price]": price_id, "line_items[0][quantity]": quantity})
        r.raise_for_status()
        data = r.json()
        return {"payment_link": data.get("url"), "id": data.get("id")}
