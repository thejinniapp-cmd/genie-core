"""core/connectors/google_sheets.py — Google Sheets connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.sheets")
BASE = "https://sheets.googleapis.com/v4/spreadsheets"

@register_connector("sheets")
class SheetsConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def read_range(self, spreadsheet_id: str, range: str) -> dict:
        """Lee un rango de celdas. range ej: 'Sheet1!A1:D10'"""
        r = httpx.get(f"{BASE}/{spreadsheet_id}/values/{range}", headers=self._h(), timeout=15)
        r.raise_for_status()
        return r.json()

    def write_range(self, spreadsheet_id: str, range: str, values: list) -> dict:
        """Escribe valores en un rango. values es lista de listas."""
        r = httpx.put(f"{BASE}/{spreadsheet_id}/values/{range}",
            headers=self._h(), timeout=15,
            params={"valueInputOption": "USER_ENTERED"},
            json={"range": range, "values": values})
        r.raise_for_status()
        return {"updated": r.json().get("updatedCells", 0)}

    def append_rows(self, spreadsheet_id: str, range: str, values: list) -> dict:
        """Agrega filas al final del rango."""
        r = httpx.post(f"{BASE}/{spreadsheet_id}/values/{range}:append",
            headers=self._h(), timeout=15,
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": values})
        r.raise_for_status()
        return {"appended": True}

    def get_spreadsheet_info(self, spreadsheet_id: str) -> dict:
        """Obtiene info y lista de hojas del spreadsheet."""
        r = httpx.get(f"{BASE}/{spreadsheet_id}", headers=self._h(),
            params={"fields": "spreadsheetId,properties,sheets.properties"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return {
            "title": data.get("properties", {}).get("title"),
            "sheets": [s["properties"]["title"] for s in data.get("sheets", [])],
        }

    def create_spreadsheet(self, title: str) -> dict:
        """Crea un nuevo spreadsheet."""
        r = httpx.post(BASE, headers=self._h(), timeout=15,
            json={"properties": {"title": title}})
        r.raise_for_status()
        data = r.json()
        return {"spreadsheet_id": data["spreadsheetId"], "title": title,
                "url": f"https://docs.google.com/spreadsheets/d/{data['spreadsheetId']}"}
