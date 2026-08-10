"""core/connectors/slack_connector.py — Slack connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.slack")
BASE = "https://slack.com/api"

@register_connector("slack")
class SlackConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['bot_token']}"}

    def send_message(self, channel: str, text: str) -> dict:
        """Envía un mensaje a un canal o usuario de Slack."""
        r = httpx.post(f"{BASE}/chat.postMessage", headers=self._h(), timeout=15,
            json={"channel": channel, "text": text})
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return {"error": data.get("error")}
        return {"sent": True, "channel": channel, "ts": data.get("ts")}

    def list_channels(self) -> list:
        """Lista los canales del workspace."""
        r = httpx.get(f"{BASE}/conversations.list", headers=self._h(), timeout=15,
            params={"limit": 20, "types": "public_channel,private_channel"})
        r.raise_for_status()
        channels = r.json().get("channels", [])
        return [{"id": c["id"], "name": c["name"], "members": c.get("num_members", 0)}
                for c in channels]

    def get_channel_history(self, channel: str, limit: int = 10) -> list:
        """Obtiene los mensajes recientes de un canal."""
        r = httpx.get(f"{BASE}/conversations.history", headers=self._h(), timeout=15,
            params={"channel": channel, "limit": limit})
        r.raise_for_status()
        messages = r.json().get("messages", [])
        return [{"user": m.get("user"), "text": m.get("text"), "ts": m.get("ts")}
                for m in messages]

    def upload_file(self, channel: str, content: str, filename: str, title: str = "") -> dict:
        """Sube un archivo de texto a un canal."""
        r = httpx.post(f"{BASE}/files.uploadV2", headers=self._h(), timeout=30,
            data={"channel": channel, "content": content, "filename": filename, "title": title or filename})
        r.raise_for_status()
        return {"uploaded": r.json().get("ok", False)}
