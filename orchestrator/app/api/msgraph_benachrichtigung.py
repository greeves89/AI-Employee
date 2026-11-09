"""Empfang der Graph-Benachrichtigungen (Plan agenten-identitaet-m365, Phase 2).

Öffentlich erreichbar — Microsoft ruft hier an, ohne Anmeldung. Deshalb:

* ``validationToken`` (beim Anlegen eines Abos) kommt unverändert als reiner Text zurück,
  sonst nichts — Microsoft verlangt genau das.
* Körper höchstens 1 MB (core/koerper_grenze, Regel in main.py; Caddy zusätzlich).
* Jede Meldung muss ein ``clientState`` tragen, das die Anlage selbst ausgestellt hat
  (core/graph_abos.abo_aus_client_state). Das wird OHNE Datenbank und Redis geprüft; nur
  was besteht, geht weiter — und dort muss das Abo zur Kennung der Meldung passen.
  Verworfenes bekommt trotzdem 202: Wer ausprobiert, erfährt nichts.
* Geantwortet wird sofort (Microsoft wartet höchstens drei Sekunden). Verarbeitet wird
  danach, höchstens ``GLEICHZEITIG`` Läufe auf einmal; ist das voll, wird verworfen —
  verlorene Meldungen holt das stündliche Nachsehen (core/graph_abos.abgleichen).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/msgraph", tags=["msgraph-benachrichtigung"])

MAX_MELDUNGEN = 100
MAX_KOERPER = 1024 * 1024
GLEICHZEITIG = 8
#: Laufende Verarbeitungen festhalten — sonst räumt der Garbage Collector sie ab.
_laeufe: set[asyncio.Task] = set()


def _validierung(token: str) -> PlainTextResponse:
    # Nur der Wert, als reiner Text (Länge begrenzt der Query-Parameter).
    return PlainTextResponse(token, media_type="text/plain")


def _angenommen() -> JSONResponse:
    return JSONResponse({"status": "accepted"}, status_code=202)


async def _gepruefte_meldungen(request: Request) -> list[tuple[str, dict]]:
    """``(abo_id, meldung)`` für jede Meldung mit echtem ``clientState`` — ohne Datenbank."""
    from app.core import graph_abos

    try:
        daten = await request.json()
    except Exception:  # noqa: BLE001
        return []
    werte = daten.get("value") if isinstance(daten, dict) else None
    if not isinstance(werte, list):
        return []
    gut = []
    for w in werte[:MAX_MELDUNGEN]:
        if isinstance(w, dict):
            abo_id = graph_abos.abo_aus_client_state(w.get("clientState"))
            if abo_id:
                gut.append((abo_id, w))
    if len(gut) < min(len(werte), MAX_MELDUNGEN):
        logger.warning("Graph-Meldungen ohne gültiges Geheimnis verworfen: %d", min(len(werte), MAX_MELDUNGEN) - len(gut))
    return gut


def _im_hintergrund(coro) -> bool:
    if len(_laeufe) >= GLEICHZEITIG:
        coro.close()
        logger.warning("Graph-Meldung verworfen: schon %d Verarbeitungen offen", len(_laeufe))
        return False
    lauf = asyncio.create_task(coro)
    _laeufe.add(lauf)
    lauf.add_done_callback(_laeufe.discard)
    return True


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


async def verarbeiten(meldungen: list[tuple[str, dict]], lebenszyklus: bool = False, redis=None) -> list[str]:
    """Vorgeprüfte Meldungen abarbeiten. Liefert je Meldung ein Ergebnis (Protokoll und Tests)."""
    from app.core import graph_abos
    from app.db.session import async_session_factory
    from app.models.agent import Agent
    from app.models.graph_abo import GraphAbo

    ergebnisse = []
    eigenes_redis = redis is None
    try:
        if eigenes_redis:
            redis = await _redis()
        async with async_session_factory() as db:
            for abo_id, m in meldungen:
                abo = await db.get(GraphAbo, abo_id)
                # Das Geheimnis gehört zu genau diesem Abo — und die Meldung muss dessen
                # Kennung bei Microsoft nennen (ein altes Geheimnis zu neuem Abo zählt nicht).
                if abo is None or not abo.subscription_id or m.get("subscriptionId") != abo.subscription_id:
                    ergebnisse.append("verworfen")
                    continue
                agent_id = abo.agent_id
                try:
                    agent = await db.get(Agent, agent_id)
                    if lebenszyklus:
                        ergebnisse.append(await _lebenszyklus(db, redis, abo, agent, str(m.get("lifecycleEvent") or "")))
                    elif m.get("changeType") == "created":
                        ergebnisse.append(await graph_abos.eingang(db, redis, agent, _message_id(m) or ""))
                    else:
                        ergebnisse.append("ignoriert")
                except Exception as e:  # noqa: BLE001 — eine Meldung darf die anderen nicht aufhalten
                    await db.rollback()
                    logger.warning("Graph-Meldung für Agent %s nicht verarbeitet: %s", agent_id, e)
                    ergebnisse.append("fehler")
    finally:
        if eigenes_redis and redis is not None:
            try:
                await redis.disconnect()
            except Exception:  # noqa: BLE001
                pass
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


async def _annehmen(request: Request, validation_token: str | None, lebenszyklus: bool):
    if validation_token:
        return _validierung(validation_token)
    meldungen = await _gepruefte_meldungen(request)
    if meldungen:
        _im_hintergrund(verarbeiten(meldungen, lebenszyklus=lebenszyklus))
    return _angenommen()


@router.post("/benachrichtigung")
async def benachrichtigung(request: Request, validationToken: str | None = Query(None, max_length=4096)):
    """Neue Nachrichten im Posteingang eines Agenten (Microsoft ruft hier an)."""
    return await _annehmen(request, validationToken, lebenszyklus=False)


@router.post("/lebenszyklus")
async def lebenszyklus(request: Request, validationToken: str | None = Query(None, max_length=4096)):
    """Lebenszyklus der Abos: neu autorisieren, entfernt, verpasste Meldungen."""
    return await _annehmen(request, validationToken, lebenszyklus=True)
