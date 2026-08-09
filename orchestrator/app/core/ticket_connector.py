"""Ticketsysteme anbinden — Matrix42 als erstes Profil.

Die offene Frage in der Roadmap war: eigenen Anschluss bauen oder über n8n gehen. Die
n8n-Brücke wäre ein zweites System für etwas, das diese Plattform selbst kann — mit
eigener Konfiguration, eigenen Zugangsdaten und einer zweiten Stelle, an der etwas
kaputtgehen kann. Deshalb hier direkt.

Nicht auf Matrix42 festgenagelt: Ticketsysteme unterscheiden sich fast nur in den
Pfaden und den Feldnamen. Beides steht deshalb in einem **Profil**, nicht im Code.
Matrix42 ist das erste; ein zweites System ist ein Wörterbuch mehr, kein neues Modul.

Was hier NICHT passiert: Tickets schließen oder löschen. Ein Agent, der ein Ticket
eigenmächtig schließt, erzeugt genau den Ärger, den die Automatisierung sparen soll.
Er darf lesen, anlegen und kommentieren — den Abschluss macht ein Mensch.
"""

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20.0
MAX_TICKETS = 50


@dataclass(frozen=True)
class TicketProfile:
    """Wie ein bestimmtes Ticketsystem angesprochen wird.

    ``fields`` bildet unsere Bezeichnungen auf die des Systems ab. Ohne diese Ebene
    stünden die Feldnamen des Herstellers im ganzen Code verteilt, und ein zweites
    System wäre ein zweiter Anschluss statt eines zweiten Eintrags.
    """

    name: str
    label: str
    list_path: str
    detail_path: str          # mit {id}
    create_path: str
    comment_path: str         # mit {id}
    fields: dict[str, str] = field(default_factory=dict)
    auth_scheme: str = "Bearer"


PROFILES: dict[str, TicketProfile] = {
    "matrix42": TicketProfile(
        name="matrix42",
        label="Matrix42 Enterprise Service Management",
        list_path="/M42Services/api/data/fragments/SPSActivityClassBase",
        detail_path="/M42Services/api/data/objects/SPSActivityClassBase/{id}",
        create_path="/M42Services/api/data/objects/SPSActivityClassBase",
        comment_path="/M42Services/api/data/objects/SPSActivityClassBase/{id}/notes",
        fields={
            "id": "ObjectID",
            "title": "Subject",
            "description": "Description",
            "status": "StateName",
            "priority": "PriorityName",
            "assignee": "ResponsibleName",
            "created_at": "CreatedDate",
        },
    ),
    # Generisch: für alles, was schlicht JSON unter /tickets spricht. Damit laesst sich
    # ein weiteres System anbinden, ohne dass hier Code dazukommt.
    "generic": TicketProfile(
        name="generic",
        label="Generisches Ticketsystem (JSON/REST)",
        list_path="/tickets",
        detail_path="/tickets/{id}",
        create_path="/tickets",
        comment_path="/tickets/{id}/comments",
        fields={
            "id": "id",
            "title": "title",
            "description": "description",
            "status": "status",
            "priority": "priority",
            "assignee": "assignee",
            "created_at": "created_at",
        },
    ),
}


def get_profile(name: str) -> TicketProfile | None:
    return PROFILES.get((name or "").strip().lower())


def normalize(raw: dict, profile: TicketProfile) -> dict:
    """Ein Ticket des Systems in unsere Form bringen.

    Fehlende Felder werden zu ``""`` statt zu ``None`` — ein leerer String ist im
    Prompt lesbar, ``None`` erzeugt beim Formatieren „None" im Text.
    """
    out = {}
    for ours, theirs in profile.fields.items():
        value = raw.get(theirs)
        out[ours] = "" if value is None else str(value)
    return out


def build_payload(profile: TicketProfile, *, title: str, description: str,
                  priority: str = "") -> dict:
    """Unsere Felder in die des Systems übersetzen."""
    payload = {
        profile.fields.get("title", "title"): title,
        profile.fields.get("description", "description"): description,
    }
    if priority:
        payload[profile.fields.get("priority", "priority")] = priority
    return payload


class TicketConnector:
    """Ein Ticketsystem, wie es die Einstellungen beschreiben."""

    def __init__(self, base_url: str, token: str, profile: TicketProfile):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.profile = profile

    @classmethod
    async def from_settings(cls, db) -> "TicketConnector | None":
        """Aus den Plattform-Einstellungen, oder ``None`` wenn nicht eingerichtet."""
        from app.services.settings_service import SettingsService

        svc = SettingsService(db)
        base_url = await svc.get("ticket_base_url") or ""
        token = await svc.get("ticket_api_token") or ""
        profile = get_profile(await svc.get("ticket_profile") or "matrix42")
        if not (base_url and token and profile):
            return None
        return cls(base_url, token, profile)

    def _headers(self) -> dict:
        return {
            "Authorization": f"{self.profile.auth_scheme} {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        # Die Adresse wird gegen dasselbe Gate geprueft wie jeder andere ausgehende
        # Aufruf — ein Ticketsystem im internen Netz ist zwar der Normalfall, aber
        # die Adresse kommt aus einer Einstellung und ist damit veraenderbar.
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    async def list_tickets(self, *, query: str = "", limit: int = 20) -> list[dict]:
        params = {"$top": str(min(limit, MAX_TICKETS))}
        if query:
            params["$filter"] = query
        data = await self._request("GET", self.profile.list_path, params=params)
        rows = data.get("value", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        return [normalize(r, self.profile) for r in rows[:limit]]

    async def get_ticket(self, ticket_id: str) -> dict | None:
        path = self.profile.detail_path.format(id=ticket_id)
        data = await self._request("GET", path)
        return normalize(data, self.profile) if isinstance(data, dict) and data else None

    async def create_ticket(self, *, title: str, description: str,
                            priority: str = "") -> dict:
        payload = build_payload(self.profile, title=title,
                                description=description, priority=priority)
        data = await self._request("POST", self.profile.create_path, json=payload)
        return normalize(data, self.profile) if isinstance(data, dict) else {}

    async def add_comment(self, ticket_id: str, text: str) -> bool:
        path = self.profile.comment_path.format(id=ticket_id)
        await self._request("POST", path, json={"Text": text, "text": text})
        return True
