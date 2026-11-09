"""Empfang der Graph-Benachrichtigungen (Plan agenten-identitaet-m365, Phase 2).

Öffentlich erreichbar — Microsoft ruft hier an, ohne Anmeldung. Deshalb:

* ``validationToken`` (beim Anlegen eines Abos) kommt unverändert als reiner Text zurück,
  sonst nichts — Microsoft verlangt genau das.
* Jede Meldung muss ein bekanntes Abonnement nennen und dessen ``clientState`` tragen
  (core/graph_abos.client_state). Sonst wird sie still verworfen. Die Antwort ist trotzdem
  202: Wer fremde Kennungen ausprobiert, erfährt nichts.
* Geantwortet wird sofort (Microsoft wartet höchstens drei Sekunden und schaltet eine
  langsame Adresse ab). Verarbeitet wird danach, in einer eigenen Datenbank-Sitzung.
* Höchstens ``MAX_MELDUNGEN`` je Aufruf; die Körpergröße begrenzt ohnehin die Middleware.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/msgraph", tags=["msgraph-benachrichtigung"])

MAX_MELDUNGEN = 100
#: Laufende Verarbeitungen festhalten — sonst räumt der Garbage Collector sie ab.
_laeufe: set[asyncio.Task] = set()


def _validierung(token: str) -> PlainTextResponse:
    # Nur der Wert, als reiner Text (Länge begrenzt der Query-Parameter).
    return PlainTextResponse(token, media_type="text/plain")


async def _meldungen(request: Request) -> list[dict]:
    try:
        daten = await request.json()
    except Exception:  # noqa: BLE001
        return []
    werte = daten.get("value") if isinstance(daten, dict) else None
    return [w for w in (werte or [])[:MAX_MELDUNGEN] if isinstance(w, dict)]


def _im_hintergrund(coro) -> None:
    lauf = asyncio.create_task(coro)
    _laeufe.add(lauf)
    lauf.add_done_callback(_laeufe.discard)


async def _redis():
    from app.config import settings
    from app.services.redis_service import RedisService

    r = RedisService(settings.redis_url)
    await r.connect()
    return r


def _message_id(meldung: dict) -> str | None:
    daten = meldung.get("resourceData") if isinstance(meldung.get("resourceData"), dict) else {}
    kennung = daten.get("id")
    if isinstance(kennung, str) and kennung:
        return kennung
    ressource = str(meldung.get("resource") or "")
    teile = ressource.rstrip("/").split("/")
    return teile[-1] if len(teile) >= 2 and teile[-2].lower() == "messages" and teile[-1] else None


async def verarbeiten(meldungen: list[dict], lebenszyklus: bool = False, redis=None) -> list[str]:
    """Geprüfte Meldungen abarbeiten. Liefert je Meldung ein Ergebnis (für Protokoll und Tests)."""
    from app.core import graph_abos
    from app.db.session import async_session_factory
    from app.models.agent import Agent

    ergebnisse = []
    eigenes_redis = redis is None
    try:
        if eigenes_redis:
            redis = await _redis()
        async with async_session_factory() as db:
            for m in meldungen:
                abo = await graph_abos.abo_zur_meldung(db, m.get("subscriptionId"))
                if abo is None or not graph_abos.client_state_passt(abo.id, m.get("clientState")):
                    ergebnisse.append("verworfen")
                    continue
                agent = await db.get(Agent, abo.agent_id)
                try:
                    if lebenszyklus:
                        ergebnisse.append(await _lebenszyklus(db, redis, abo, agent, str(m.get("lifecycleEvent") or "")))
                    elif m.get("changeType") == "created":
                        ergebnisse.append(await graph_abos.eingang(db, redis, agent, _message_id(m) or ""))
                    else:
                        ergebnisse.append("ignoriert")
                except Exception as e:  # noqa: BLE001 — eine Meldung darf die anderen nicht aufhalten
                    await db.rollback()
                    logger.warning("Graph-Meldung für Agent %s nicht verarbeitet: %s", abo.agent_id, e)
                    ergebnisse.append("fehler")
    finally:
        if eigenes_redis and redis is not None:
            try:
                await redis.disconnect()
            except Exception:  # noqa: BLE001
                pass
    if any(e == "verworfen" for e in ergebnisse):
        logger.warning("Graph-Meldungen ohne passendes Abo/Geheimnis verworfen: %d", ergebnisse.count("verworfen"))
    return ergebnisse


async def _lebenszyklus(db, redis, abo, agent, ereignis: str) -> str:
    from app.core import graph_abos

    if ereignis == "missed":
        await graph_abos.nachholen(db, redis, abo)
        return "nachgeholt"
    if ereignis == "reauthorizationRequired":
        abo.ablauf = graph_abos.jetzt()          # → einrichten verlängert (PATCH) und autorisiert neu
    elif ereignis == "subscriptionRemoved":
        abo.subscription_id = None               # → einrichten legt neu an
        abo.ablauf = None
    else:
        return "ignoriert"
    await db.commit()
    await graph_abos.einrichten(db, agent)
    return ereignis


@router.post("/benachrichtigung")
async def benachrichtigung(request: Request, validationToken: str | None = Query(None, max_length=4096)):
    """Neue Nachrichten im Posteingang eines Agenten (Microsoft ruft hier an)."""
    if validationToken:
        return _validierung(validationToken)
    meldungen = await _meldungen(request)
    if meldungen:
        _im_hintergrund(verarbeiten(meldungen))
    return JSONResponse({"status": "accepted"}, status_code=202)


@router.post("/lebenszyklus")
async def lebenszyklus(request: Request, validationToken: str | None = Query(None, max_length=4096)):
    """Lebenszyklus der Abos: neu autorisieren, entfernt, verpasste Meldungen."""
    if validationToken:
        return _validierung(validationToken)
    meldungen = await _meldungen(request)
    if meldungen:
        _im_hintergrund(verarbeiten(meldungen, lebenszyklus=True))
    return JSONResponse({"status": "accepted"}, status_code=202)
