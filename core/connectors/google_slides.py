"""core/connectors/google_slides.py — Google Slides connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.slides")
BASE = "https://slides.googleapis.com/v1/presentations"

@register_connector("slides")
class SlidesConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def get_presentation(self, presentation_id: str) -> dict:
        """Obtiene la estructura de una presentación."""
        r = httpx.get(f"{BASE}/{presentation_id}", headers=self._h(), timeout=15)
        r.raise_for_status()
        data = r.json()
        slides = []
        for i, slide in enumerate(data.get("slides", [])[:10]):
            texts = []
            for el in slide.get("pageElements", []):
                shape = el.get("shape", {})
                for te in shape.get("text", {}).get("textElements", []):
                    content = te.get("textRun", {}).get("content", "").strip()
                    if content:
                        texts.append(content)
            slides.append({"slide": i + 1, "texts": texts})
        return {"title": data.get("title"), "slides": slides,
                "url": f"https://docs.google.com/presentation/d/{presentation_id}"}

    def create_presentation(self, title: str) -> dict:
        """Crea una nueva presentación."""
        r = httpx.post(BASE, headers=self._h(), timeout=15, json={"title": title})
        r.raise_for_status()
        data = r.json()
        return {"presentation_id": data["presentationId"], "title": title,
                "url": f"https://docs.google.com/presentation/d/{data['presentationId']}"}

    def replace_text(self, presentation_id: str, old_text: str, new_text: str) -> dict:
        """Reemplaza texto en toda la presentación."""
        r = httpx.post(f"{BASE}/{presentation_id}:batchUpdate", headers=self._h(), timeout=15,
            json={"requests": [{"replaceAllText": {
                "containsText": {"text": old_text, "matchCase": False},
                "replaceText": new_text
            }}]})
        r.raise_for_status()
        return {"replaced": True, "presentation_id": presentation_id}
