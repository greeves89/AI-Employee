"""Rückruf-Adresse für Teams-Anrufe — die EINE Adresse, die in Azure eingetragen wird.

Microsoft ruft hier an, sobald sich an einem Anruf etwas ändert: Bot ist beigetreten,
eine Aufnahme ist fertig, jemand hat aufgelegt. Die Adresse ist damit öffentlich
erreichbar — deshalb wird jede Benachrichtigung geprüft, bevor irgendetwas passiert.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import teams_calling as tc
from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams/calling", tags=["teams-calling"])


class JoinRequest(BaseModel):
    join_url: str
    agent_id: str
    display_name: str = "AI Employee"


@router.get("/setup")
async def calling_setup(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Alles, was die Einrichtungs-Karte braucht — inklusive der Adresse zum Kopieren.

    Bewusst mit ``https_ok``: Microsoft ruft ausschliesslich HTTPS zurück. Steht die
    Anlage nur unter http, kommt keine einzige Benachrichtigung an, ohne dass hier
    ein Fehler auftauchen würde. Das gehört VOR die Einrichtung, nicht danach.
    """
    cfg = await tc.load_settings(db)
    base = settings.oauth_redirect_base_url
    return {
        "callback_url": tc.callback_url(base),
        "https_ok": tc.public_base_is_https(base),
        "app_id": cfg[tc.APP_ID],
        "tenant_id": cfg[tc.TENANT_ID],
        "has_secret": bool(cfg[tc.APP_SECRET]),
        "configured": tc.is_configured(cfg),
        "enabled": tc.is_enabled(cfg),
        "permissions": [
            {"name": name, "why": why} for name, why in tc.REQUIRED_PERMISSIONS
        ],
    }


@router.post("/test")
async def calling_test(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Prüfen, ob Azure fertig eingerichtet ist — vor dem ersten echten Termin.

    Ohne diesen Knopf merkt man einen fehlenden Zustimmungsklick erst, wenn der
    Agent einer Besprechung fernbleibt und niemand weiss, warum.
    """
    return await tc.check_setup(await tc.load_settings(db))


@router.post("/join")
async def calling_join(
    body: JoinRequest,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Einen Agenten in einen Termin holen (Beitrittslink aus der Einladung)."""
    cfg = await tc.load_settings(db)
    if not tc.is_enabled(cfg):
        raise HTTPException(status_code=503, detail="Teams-Anrufe sind nicht eingerichtet oder aus.")

    call = await tc.join_meeting(
        cfg, join_url=body.join_url,
        base_url=settings.oauth_redirect_base_url,
        display_name=body.display_name,
    )
    if not call:
        raise HTTPException(status_code=502, detail="Beitritt fehlgeschlagen — siehe Protokoll.")

    # Zuordnung merken: die Rueckrufe von Microsoft tragen nur die Anruf-ID, nicht
    # den Agenten. Ohne diese Notiz wuesste der Rueckruf nicht, wer sprechen soll.
    try:
        redis = getattr(db, "_redis", None) or None
        from app.api import ws as ws_module
        client = getattr(ws_module, "_redis", None)
        if client:
            await client.client.set(f"teams:call:{call.get('id')}", body.agent_id, ex=6 * 3600)
    except Exception:  # noqa: BLE001
        logger.warning("[Teams-Anruf] Zuordnung Anruf→Agent nicht gespeichert")

    return {"call_id": call.get("id"), "state": call.get("state"), "agent_id": body.agent_id}


@router.post("/callback")
async def calling_callback(
    request: Request,
    validationToken: str | None = Query(None),
):
    """Benachrichtigungen von Microsoft.

    Zwei Dinge passieren hier, und beide sind Vorschrift:

    1. **Abonnement-Prüfung.** Beim Eintragen schickt Microsoft ein
       ``validationToken`` und erwartet es unverändert als reinen Text zurück.
       Antwortet man mit JSON, gilt die Adresse als ungültig.
    2. **Ereignisse.** Beigetreten, Aufnahme fertig, aufgelegt.

    Es wird IMMER mit 202 geantwortet, sobald die Nachricht lesbar war: Microsoft
    wiederholt sonst und schaltet die Adresse nach wiederholten Fehlern ab.
    """
    from fastapi.responses import PlainTextResponse

    if validationToken:
        logger.info("[Teams-Anruf] Abonnement-Pruefung beantwortet")
        return PlainTextResponse(validationToken)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Kein lesbares JSON")

    for note in (payload.get("value") or []):
        try:
            await _handle_notification(note)
        except Exception as e:  # noqa: BLE001 — nie 5xx an Microsoft
            logger.warning("[Teams-Anruf] Ereignis nicht verarbeitet: %s", scrub_log(str(e)))

    return {"status": "accepted"}


async def _handle_notification(note: dict) -> None:
    """Ein einzelnes Ereignis.

    Bewusst schlank gehalten: Was der Agent im Termin SAGT, entscheidet die
    Sprachschicht — hier wird nur übersetzt, was Microsoft meldet.
    """
    resource = str(note.get("resourceUrl") or note.get("resource") or "")
    data = note.get("resourceData") or {}
    state = str(data.get("state") or "")
    call_id = str(data.get("id") or "").strip()

    if state == "established":
        logger.info("[Teams-Anruf] Agent ist dem Termin beigetreten (%s)", scrub_log(call_id[:12]))
    elif state == "terminated":
        logger.info("[Teams-Anruf] Termin verlassen (%s)", scrub_log(call_id[:12]))
    elif "recordResponse" in resource or data.get("recordingLocation"):
        logger.info("[Teams-Anruf] Aufnahme liegt vor (%s)", scrub_log(call_id[:12]))
    else:
        logger.debug("[Teams-Anruf] Ereignis: %s / %s", scrub_log(resource[:60]), scrub_log(state))
