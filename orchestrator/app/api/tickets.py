"""Ticketsystem-Endpunkte fuer Agenten (Matrix42 o.a.).

Agenten-seitig, nicht nutzer-seitig: den Anschluss richtet ein Administrator EINMAL in
den Einstellungen ein, danach arbeiten alle Agenten darueber. Ein Zugang je Agent
haette bedeutet, dasselbe Token an neun Stellen zu pflegen.

Bewusst ohne Schliessen und Loeschen — ein Agent, der ein Ticket eigenmaechtig
schliesst, erzeugt genau den Aerger, den die Automatisierung sparen soll.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import gebundene_freigabe
from app.core.faehigkeiten import verlangen
from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import verify_agent_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tickets", tags=["tickets"])


class CreateTicket(BaseModel):
    title: str
    description: str = ""
    priority: str = ""


class AddComment(BaseModel):
    text: str


async def _connector(db: AsyncSession):
    from app.core.ticket_connector import TicketConnector

    conn = await TicketConnector.from_settings(db)
    if conn is None:
        raise HTTPException(
            status_code=503,
            detail="Kein Ticketsystem eingerichtet (Einstellungen → Ticketsystem).",
        )
    return conn


@router.get("/")
async def list_tickets(
    query: str = Query("", description="Systemeigener Filterausdruck"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _auth: dict = Depends(verify_agent_token),
):
    conn = await _connector(db)
    try:
        return {"profile": conn.profile.name, "tickets": await conn.list_tickets(query=query, limit=limit)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ticketsystem antwortet nicht: {e}")


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: dict = Depends(verify_agent_token),
):
    conn = await _connector(db)
    try:
        ticket = await conn.get_ticket(ticket_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ticketsystem antwortet nicht: {e}")
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    return ticket


@router.post("/", status_code=201)
async def create_ticket(
    body: CreateTicket,
    db: AsyncSession = Depends(get_db),
    _auth: dict = Depends(verify_agent_token),
):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title ist erforderlich")
    # Wirkung nach aussen (Ticketsystem): „Verboten“ sperrt, „Freigabe“ stellt GENAU
    # dieses Ticket zur Freigabe — angelegt wird es dann vom Server, nicht vom Agenten.
    if await verlangen(db, _auth["agent_id"], "external_api") == "ask":
        daten = {"title": body.title.strip(), "description": body.description, "priority": body.priority}
        return JSONResponse(status_code=202, content=await gebundene_freigabe.anfragen(
            db, _auth["agent_id"], "ticket_anlegen", daten, werkzeug="ticket_create",
            titel=f"Ticket „{daten['title'][:120]}“ anlegen",
            felder=[("Titel", daten["title"]), ("Priorität", daten["priority"] or "—"),
                    ("Beschreibung", daten["description"] or "—")]))
    conn = await _connector(db)
    try:
        created = await conn.create_ticket(
            title=body.title.strip(), description=body.description, priority=body.priority
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Anlegen fehlgeschlagen: {e}")
    logger.info("[Tickets] Agent %s hat ein Ticket angelegt", scrub_log(_auth.get("agent_id")))
    return created


@router.post("/{ticket_id}/comment")
async def comment_ticket(
    ticket_id: str,
    body: AddComment,
    db: AsyncSession = Depends(get_db),
    _auth: dict = Depends(verify_agent_token),
):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text ist erforderlich")
    if await verlangen(db, _auth["agent_id"], "external_api") == "ask":
        daten = {"ticket_id": ticket_id, "text": body.text.strip()}
        return JSONResponse(status_code=202, content=await gebundene_freigabe.anfragen(
            db, _auth["agent_id"], "ticket_kommentar", daten, werkzeug="ticket_comment",
            titel=f"Ticket {ticket_id} kommentieren",
            felder=[("Ticket", ticket_id), ("Kommentar", daten["text"])]))
    conn = await _connector(db)
    try:
        await conn.add_comment(ticket_id, body.text.strip())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Kommentar fehlgeschlagen: {e}")
    return {"ticket_id": ticket_id, "status": "ok"}


# ── Gebundene Handlungen: Ausführung nach der Freigabe (core/gebundene_freigabe) ──

@gebundene_freigabe.ausfuehrer("ticket_anlegen")
async def _ticket_anlegen(db: AsyncSession, daten: dict, agent_id: str) -> str:
    conn = await _connector(db)
    created = await conn.create_ticket(title=daten["title"], description=daten.get("description", ""),
                                       priority=daten.get("priority", ""))
    logger.info("[Tickets] Ticket nach Freigabe angelegt (Agent %s)", scrub_log(agent_id))
    return f"Ticket angelegt: {(created or {}).get('id') or (created or {}).get('number') or 'ok'}"


@gebundene_freigabe.ausfuehrer("ticket_kommentar")
async def _ticket_kommentar(db: AsyncSession, daten: dict, agent_id: str) -> str:
    conn = await _connector(db)
    await conn.add_comment(daten["ticket_id"], daten["text"])
    return f"Kommentar an Ticket {daten['ticket_id']} geschrieben"
