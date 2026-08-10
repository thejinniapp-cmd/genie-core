"""core/connectors/google_docs.py — Google Docs connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.docs")
BASE = "https://docs.googleapis.com/v1/documents"

@register_connector("docs")
class DocsConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def get_document(self, document_id: str) -> dict:
        """Lee el contenido de un Google Doc."""
        r = httpx.get(f"{BASE}/{document_id}", headers=self._h(), timeout=15)
        r.raise_for_status()
        data = r.json()
        # Extraer texto plano
        text = ""
        for el in data.get("body", {}).get("content", []):
            for para in el.get("paragraph", {}).get("elements", []):
                text += para.get("textRun", {}).get("content", "")
        return {"title": data.get("title"), "text": text[:5000], "document_id": document_id}

    def create_document(self, title: str) -> dict:
        """Crea un nuevo Google Doc."""
        r = httpx.post(BASE, headers=self._h(), timeout=15, json={"title": title})
        r.raise_for_status()
        data = r.json()
        return {"document_id": data["documentId"], "title": title,
                "url": f"https://docs.google.com/document/d/{data['documentId']}"}

    def append_text(self, document_id: str, text: str) -> dict:
        """Agrega texto al final del documento."""
        r = httpx.post(f"{BASE}/{document_id}:batchUpdate", headers=self._h(), timeout=15,
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]})
        r.raise_for_status()
        return {"updated": True, "document_id": document_id}

    def replace_text(self, document_id: str, old_text: str, new_text: str) -> dict:
        """Reemplaza texto en el documento."""
        r = httpx.post(f"{BASE}/{document_id}:batchUpdate", headers=self._h(), timeout=15,
            json={"requests": [{"replaceAllText": {
                "containsText": {"text": old_text, "matchCase": True},
                "replaceText": new_text
            }}]})
        r.raise_for_status()
        return {"replaced": True}
