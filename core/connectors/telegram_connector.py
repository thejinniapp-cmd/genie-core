"""core/connectors/telegram_connector.py — Telegram Bot connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.telegram")

@register_connector("telegram")
class TelegramConnector(BaseConnector):
    def _base(self): return f"https://api.telegram.org/bot{self.credentials['bot_token']}"

    def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> dict:
        """Envía un mensaje a un chat de Telegram."""
        r = httpx.post(f"{self._base()}/sendMessage", timeout=15,
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        r.raise_for_status()
        return {"sent": True, "chat_id": chat_id}

    def get_updates(self, limit: int = 10) -> list:
        """Obtiene los mensajes recientes recibidos por el bot."""
        r = httpx.get(f"{self._base()}/getUpdates", timeout=15,
            params={"limit": limit, "allowed_updates": ["message"]})
        r.raise_for_status()
        updates = r.json().get("result", [])
        return [{"from": u.get("message", {}).get("from", {}).get("username"),
                 "text": u.get("message", {}).get("text", ""),
                 "chat_id": u.get("message", {}).get("chat", {}).get("id")}
                for u in updates if u.get("message")]

    def get_bot_info(self) -> dict:
        """Obtiene información del bot."""
        r = httpx.get(f"{self._base()}/getMe", timeout=10)
        r.raise_for_status()
        return r.json().get("result", {})

    def send_photo(self, chat_id: str, photo_url: str, caption: str = "") -> dict:
        """Envía una imagen a un chat."""
        r = httpx.post(f"{self._base()}/sendPhoto", timeout=20,
            json={"chat_id": chat_id, "photo": photo_url, "caption": caption})
        r.raise_for_status()
        return {"sent": True}
