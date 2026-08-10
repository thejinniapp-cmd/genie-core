"""core/connectors/google_calendar.py — Google Calendar + Meet connector"""
import logging
import httpx
from core.connectors.executor import BaseConnector, register_connector

log = logging.getLogger("genie.connectors.calendar")
BASE = "https://www.googleapis.com/calendar/v3"


@register_connector("calendar")
class CalendarConnector(BaseConnector):
    def _h(self): return {"Authorization": f"Bearer {self.credentials['access_token']}"}

    def list_events(self, calendar_id: str = "primary", max_results: int = 10,
                    time_min: str = None, time_max: str = None) -> list:
        """Lista eventos del calendario. time_min/time_max en formato RFC3339."""
        params = {
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        r = httpx.get(f"{BASE}/calendars/{calendar_id}/events",
                      headers=self._h(), params=params, timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        result = []
        for e in items:
            start = e.get("start", {})
            end = e.get("end", {})
            result.append({
                "id": e.get("id"),
                "summary": e.get("summary", "(sin título)"),
                "description": e.get("description", ""),
                "start": start.get("dateTime") or start.get("date"),
                "end": end.get("dateTime") or end.get("date"),
                "location": e.get("location", ""),
                "meet_link": e.get("hangoutLink", ""),
                "attendees": [a.get("email") for a in e.get("attendees", [])],
                "status": e.get("status", ""),
                "html_link": e.get("htmlLink", ""),
            })
        return result

    def get_event(self, event_id: str, calendar_id: str = "primary") -> dict:
        """Obtiene detalle completo de un evento."""
        r = httpx.get(f"{BASE}/calendars/{calendar_id}/events/{event_id}",
                      headers=self._h(), timeout=15)
        r.raise_for_status()
        e = r.json()
        start = e.get("start", {})
        end = e.get("end", {})
        return {
            "id": e.get("id"),
            "summary": e.get("summary", "(sin título)"),
            "description": e.get("description", ""),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "location": e.get("location", ""),
            "meet_link": e.get("hangoutLink", ""),
            "attendees": [{"email": a.get("email"), "status": a.get("responseStatus")}
                          for a in e.get("attendees", [])],
            "html_link": e.get("htmlLink", ""),
        }

    def create_event(self, summary: str, start_datetime: str, end_datetime: str,
                     description: str = "", attendees: list = None,
                     location: str = "", add_meet: bool = True,
                     calendar_id: str = "primary") -> dict:
        """Crea un evento. Si add_meet=True agrega Google Meet automáticamente."""
        body: dict = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start_datetime, "timeZone": "America/Mexico_City"},
            "end": {"dateTime": end_datetime, "timeZone": "America/Mexico_City"},
        }
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]
        if add_meet:
            body["conferenceData"] = {
                "createRequest": {"requestId": f"genie-{summary[:20]}",
                                  "conferenceSolutionKey": {"type": "hangoutsMeet"}}
            }
        params = {"conferenceDataVersion": 1} if add_meet else {}
        r = httpx.post(f"{BASE}/calendars/{calendar_id}/events",
                       headers=self._h(), json=body, params=params, timeout=15)
        r.raise_for_status()
        e = r.json()
        return {
            "id": e.get("id"),
            "summary": e.get("summary"),
            "start": e.get("start", {}).get("dateTime"),
            "end": e.get("end", {}).get("dateTime"),
            "meet_link": e.get("hangoutLink", ""),
            "html_link": e.get("htmlLink", ""),
        }

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> dict:
        """Elimina un evento del calendario."""
        r = httpx.delete(f"{BASE}/calendars/{calendar_id}/events/{event_id}",
                         headers=self._h(), timeout=15)
        r.raise_for_status()
        return {"deleted": True, "event_id": event_id}

    def list_calendars(self) -> list:
        """Lista los calendarios disponibles en la cuenta."""
        r = httpx.get(f"{BASE}/users/me/calendarList", headers=self._h(), timeout=15)
        r.raise_for_status()
        return [{"id": c["id"], "summary": c.get("summary", ""), "primary": c.get("primary", False)}
                for c in r.json().get("items", [])]
