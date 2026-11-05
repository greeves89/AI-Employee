"""Tägliche Auswertung für „Aus meiner Arbeit lernen“ (#1023).

Läuft stündlich an, wertet aber je Mensch höchstens einmal am Tag aus (und nur,
wenn er den Schalter eingeschaltet hat). Der Agent bekommt die Zusammenfassung
über seinen normalen Chat (Budgetprüfung wie bei jeder Nachricht) in einem
eigenen Faden und darf dabei nur nachdenken, nichts ausführen.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

_INTERVALL_SEKUNDEN = 3600
_ABSTAND = timedelta(hours=20)
#: Scheitert ein Lauf (Agent aus, Budget leer), versuchen wir es nicht stündlich neu.
_NEUER_VERSUCH_SEKUNDEN = 6 * 3600
FADEN = "bridge-lernen"


def _aware(t: datetime) -> datetime:
    """SQLite liefert Zeitstempel ohne Zone, Postgres mit — verglichen wird in UTC."""
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class BridgeLernenDienst:
    def __init__(self, session_factory, redis=None) -> None:
        self._sf = session_factory
        self._redis = redis
        self._running = True
        self._versuch: dict[str, float] = {}

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        logger.info("[Bridge-Lernen] Auswertung gestartet (stündlich, je Nutzer einmal am Tag)")
        await asyncio.sleep(240)
        while self._running:
            try:
                await self.einmal()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[Bridge-Lernen] Durchlauf gescheitert", exc_info=True)
            await asyncio.sleep(_INTERVALL_SEKUNDEN)

    async def einmal(self, jetzt: datetime | None = None) -> int:
        """Ein Durchlauf: Altes löschen, fällige Nutzer auswerten. Gibt die Zahl der Auswertungen zurück."""
        from app.core import bridge_lernen as bl
        from app.models.bridge_lernen import BridgeLernen

        jetzt = jetzt or datetime.now(timezone.utc)
        async with self._sf() as db:
            await bl.protokoll_aufraeumen(db, jetzt)
            await db.commit()
            faellig = [z.user_id for z in (await db.execute(select(BridgeLernen).where(
                BridgeLernen.aktiv.is_(True)))).scalars().all()
                if z.ausgewertet_at is None or _aware(z.ausgewertet_at) < jetzt - _ABSTAND]
        gemacht = 0
        for user_id in faellig:
            if time.time() - self._versuch.get(user_id, 0) < _NEUER_VERSUCH_SEKUNDEN:
                continue
            try:
                if await self.auswerten(user_id, jetzt):
                    gemacht += 1
            except Exception:  # noqa: BLE001 — ein Mensch scheitert, die anderen laufen weiter
                logger.warning("[Bridge-Lernen] Auswertung gescheitert", exc_info=True)
        return gemacht

    async def auswerten(self, user_id: str, jetzt: datetime | None = None) -> dict:
        """Eine Auswertung für einen Menschen. Gibt ``{"status": …, "vorschlaege": n}`` zurück.

        Status: ``ok`` (Vorschläge gespeichert oder nichts anzubieten), ``zu_wenig``
        (noch keine Gewohnheit erkennbar), ``kein_agent``, ``fehler`` (neuer Versuch später).
        """
        from app.core import bridge_lernen as bl
        from app.core import bridge_mitdenker as bm
        from app.models.agent import Agent
        from app.services.agent_chat_bridge import ask_agent_via_chat

        jetzt = jetzt or datetime.now(timezone.utc)
        async with self._sf() as db:
            schalter = await bl.schalter_holen(db, user_id)
            if schalter is None or not schalter.aktiv:
                return {"status": "aus", "vorschlaege": 0}
            z = await bl.zusammenfassung(db, user_id, jetzt)
            if not bl.hat_stoff(z):
                schalter.ausgewertet_at = jetzt
                await db.commit()
                return {"status": "zu_wenig", "vorschlaege": 0}
            agent = await db.get(Agent, schalter.agent_id) if schalter.agent_id else None
            if (agent is None or str(agent.user_id) != str(user_id)
                    or not bm.kann_nur_ansehen((agent.config or {}).get("agent_version"))):
                # Kein Abbild mit Nur-ansehen-Zug: lieber nicht auswerten als mit allen Werkzeugen.
                return {"status": "kein_agent", "vorschlaege": 0}
            agent_id = str(agent.id)
            regeln = [{"wenn_app": r.wenn_app, "wenn_titel": r.wenn_titel}
                      for r in await bm.regeln_laden(db, user_id)]
        self._versuch[user_id] = time.time()
        antwort = await ask_agent_via_chat(
            self._redis, agent_id, bl.auswertung_anweisung(z, regeln), source="bridge",
            timeout=300.0, chat_session_id=FADEN, nur_ansehen=True,
        )
        if str(antwort or "").startswith("[Fehler"):
            return {"status": "fehler", "vorschlaege": 0}
        neue = bl.vorschlaege_lesen(antwort, z)
        async with self._sf() as db:
            gespeichert = await bl.vorschlaege_speichern(db, user_id, agent_id, neue)
            schalter = await bl.schalter_holen(db, user_id)
            if schalter is not None:
                schalter.ausgewertet_at = jetzt
            await db.commit()
            for v in gespeichert:
                await db.refresh(v)
            text = bl.rueckfrage_text(gespeichert) if gespeichert else ""
        self._versuch.pop(user_id, None)
        if text:
            await _in_den_chat(agent_id, text)
        return {"status": "ok", "vorschlaege": len(gespeichert)}


async def _in_den_chat(agent_id: str, text: str) -> None:
    """Die Rückfrage als Nachricht des Agenten ins Gespräch „Bridge“ legen."""
    from app.api.computer_use import BRIDGE_GESPRAECH, BRIDGE_GESPRAECH_TITEL
    from app.db.session import async_session_factory
    from app.models.chat_session import ChatSession
    from app.services.chat_persistence import upsert_chat_message

    try:
        async with async_session_factory() as db:
            zeile = await db.scalar(select(ChatSession).where(
                ChatSession.agent_id == agent_id, ChatSession.session_id == BRIDGE_GESPRAECH))
            if zeile is None:
                db.add(ChatSession(agent_id=agent_id, session_id=BRIDGE_GESPRAECH, title=BRIDGE_GESPRAECH_TITEL))
                await db.commit()
        await upsert_chat_message(agent_id, BRIDGE_GESPRAECH, uuid.uuid4().hex[:12], "assistant",
                                  content=text, meta={"source": "bridge-lernen"})
    except Exception:  # noqa: BLE001 — die Vorschläge stehen auch in den Einstellungen
        logger.warning("[Bridge-Lernen] Rückfrage nicht in den Chat gelegt", exc_info=True)
