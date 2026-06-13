"""
core/connectors/gmail.py
========================
Conector Gmail para Genie.

Acciones disponibles:
- send_email(to, subject, body, html?)
- list_emails(max_results?, label?)
- search_emails(query, max_results?)
- get_email(message_id)
- create_draft(to, subject, body)
"""

import base64
import logging
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.gmail")

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


@register_connector("gmail")
class GmailConnector(BaseConnector):
    """
    Conector Gmail.
    Credenciales requeridas: access_token (OAuth2)
    """

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
    ) -> dict:
        """Envía un email desde la cuenta conectada."""
        msg = MIMEMultipart("alternative")
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html" if html else "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = httpx.post(
            f"{GMAIL_API}/messages/send",
            headers=self._headers(),
            json={"raw": raw},
            timeout=20,
        )
        resp.raise_for_status()
        log.info(f"[gmail] Email sent to {to}")
        return {"sent": True, "to": to, "subject": subject}

    def list_emails(self, max_results: int = 10, label: str = "INBOX") -> list[dict]:
        """Lista los emails más recientes."""
        resp = httpx.get(
            f"{GMAIL_API}/messages",
            headers=self._headers(),
            params={"maxResults": max_results, "labelIds": label},
            timeout=15,
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])

        result = []
        for m in messages[:5]:  # limitar detalle a 5
            detail = self._get_message_metadata(m["id"])
            if detail:
                result.append(detail)

        return result

    def search_emails(self, query: str, max_results: int = 10) -> list[dict]:
        """Busca emails por query."""
        resp = httpx.get(
            f"{GMAIL_API}/messages",
            headers=self._headers(),
            params={"q": query, "maxResults": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])

        result = []
        for m in messages[:5]:
            detail = self._get_message_metadata(m["id"])
            if detail:
                result.append(detail)

        return result

    def get_email(self, message_id: str) -> dict:
        """Obtiene el contenido completo de un email."""
        resp = httpx.get(
            f"{GMAIL_API}/messages/{message_id}",
            headers=self._headers(),
            params={"format": "full"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
        body = self._extract_body(data.get("payload", {}))

        return {
            "id": message_id,
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body[:3000],
        }

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        """Crea un borrador de email."""
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        resp = httpx.post(
            f"{GMAIL_API}/drafts",
            headers=self._headers(),
            json={"message": {"raw": raw}},
            timeout=15,
        )
        resp.raise_for_status()
        return {"draft_id": resp.json().get("id"), "to": to, "subject": subject}

    def _get_message_metadata(self, message_id: str) -> dict | None:
        try:
            resp = httpx.get(
                f"{GMAIL_API}/messages/{message_id}",
                headers=self._headers(),
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
            return {
                "id": message_id,
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(sin asunto)"),
                "date": headers.get("Date", ""),
            }
        except Exception:
            return None

    def _extract_body(self, payload: dict) -> str:
        """Extrae el texto del body de un mensaje."""
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            if part.get("mimeType") in ("text/plain", "text/html"):
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "gmail_send_email",
                    "description": "Envía un email desde la cuenta Gmail conectada",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string", "description": "Email del destinatario"},
                            "subject": {"type": "string", "description": "Asunto del email"},
                            "body": {"type": "string", "description": "Cuerpo del email"},
                            "html": {"type": "boolean", "description": "Si el cuerpo es HTML"},
                        },
                        "required": ["to", "subject", "body"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "gmail_search_emails",
                    "description": "Busca emails en la bandeja de entrada",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Búsqueda Gmail (ej: 'from:cliente@empresa.com')"},
                            "max_results": {"type": "integer", "description": "Máximo de resultados"},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]
