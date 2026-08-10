"""core/connectors/google_drive.py — Google Drive connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.drive")
BASE = "https://www.googleapis.com/drive/v3"

@register_connector("drive")
class DriveConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def list_files(self, query: str = "", max_results: int = 10) -> list:
        """Lista archivos en Drive. query ej: \"name contains 'presupuesto'\" """
        params = {"pageSize": max_results, "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"}
        if query:
            params["q"] = query
        r = httpx.get(f"{BASE}/files", headers=self._h(), params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("files", [])

    def get_file_info(self, file_id: str) -> dict:
        """Obtiene metadata de un archivo."""
        r = httpx.get(f"{BASE}/files/{file_id}",
            headers=self._h(), timeout=15,
            params={"fields": "id,name,mimeType,size,modifiedTime,webViewLink,parents"})
        r.raise_for_status()
        return r.json()

    def search_files(self, name: str) -> list:
        """Busca archivos por nombre."""
        return self.list_files(query=f"name contains '{name}'")

    def create_folder(self, name: str, parent_id: str = None) -> dict:
        """Crea una carpeta en Drive."""
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            body["parents"] = [parent_id]
        r = httpx.post(f"{BASE}/files", headers=self._h(), json=body, timeout=15)
        r.raise_for_status()
        data = r.json()
        return {"folder_id": data["id"], "name": name,
                "url": f"https://drive.google.com/drive/folders/{data['id']}"}

    def copy_file(self, file_id: str, new_name: str = None, parent_id: str = None) -> dict:
        """Crea una copia de un archivo en Drive."""
        body = {}
        if new_name:
            body["name"] = new_name
        if parent_id:
            body["parents"] = [parent_id]
        r = httpx.post(f"{BASE}/files/{file_id}/copy", headers=self._h(), json=body, timeout=15)
        r.raise_for_status()
        data = r.json()
        return {"file_id": data["id"], "name": data.get("name", ""), "webViewLink": data.get("webViewLink", "")}

    def share_file(self, file_id: str, email: str, role: str = "reader") -> dict:
        """Comparte un archivo con un email. role: reader, writer, commenter"""
        r = httpx.post(f"{BASE}/files/{file_id}/permissions", headers=self._h(), timeout=15,
            json={"type": "user", "role": role, "emailAddress": email})
        r.raise_for_status()
        return {"shared": True, "file_id": file_id, "email": email, "role": role}
