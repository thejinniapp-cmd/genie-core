"""core/connectors/hubspot_connector.py — HubSpot connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.hubspot")
BASE = "https://api.hubapi.com"

@register_connector("hubspot")
class HubSpotConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def list_contacts(self, limit: int = 10) -> list:
        """Lista contactos de HubSpot."""
        r = httpx.get(f"{BASE}/crm/v3/objects/contacts", headers=self._h(), timeout=15,
            params={"limit": limit, "properties": "email,firstname,lastname,company,phone"})
        r.raise_for_status()
        results = r.json().get("results", [])
        return [{"id": c["id"], **c.get("properties", {})} for c in results]

    def search_contacts(self, email: str) -> list:
        """Busca contactos por email."""
        r = httpx.post(f"{BASE}/crm/v3/objects/contacts/search", headers=self._h(), timeout=15,
            json={"filterGroups": [{"filters": [{"propertyName": "email",
                "operator": "EQ", "value": email}]}],
                "properties": ["email", "firstname", "lastname", "company"]})
        r.raise_for_status()
        return r.json().get("results", [])

    def create_contact(self, email: str, firstname: str = "", lastname: str = "",
                       company: str = "", phone: str = "") -> dict:
        """Crea un nuevo contacto en HubSpot."""
        r = httpx.post(f"{BASE}/crm/v3/objects/contacts", headers=self._h(), timeout=15,
            json={"properties": {"email": email, "firstname": firstname,
                "lastname": lastname, "company": company, "phone": phone}})
        r.raise_for_status()
        data = r.json()
        return {"id": data["id"], "email": email}

    def list_deals(self, limit: int = 10) -> list:
        """Lista deals/oportunidades."""
        r = httpx.get(f"{BASE}/crm/v3/objects/deals", headers=self._h(), timeout=15,
            params={"limit": limit, "properties": "dealname,amount,dealstage,closedate"})
        r.raise_for_status()
        results = r.json().get("results", [])
        return [{"id": d["id"], **d.get("properties", {})} for d in results]

    def create_note(self, contact_id: str, note: str) -> dict:
        """Crea una nota asociada a un contacto."""
        r = httpx.post(f"{BASE}/crm/v3/objects/notes", headers=self._h(), timeout=15,
            json={"properties": {"hs_note_body": note, "hs_timestamp": ""},
                  "associations": [{"to": {"id": contact_id},
                      "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                 "associationTypeId": 202}]}]})
        r.raise_for_status()
        return {"created": True, "note_id": r.json().get("id")}

    def sync(self) -> dict:
        """Sincroniza contactos, empresas y deals hacia Genie."""
        from core.sync.engine import run_sync
        return run_sync(self.org_id, "hubspot", self.credentials)
