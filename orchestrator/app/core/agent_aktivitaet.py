"""„Zuletzt aktiv“ je Agent (``agents.last_active_at``).

Die Listenansicht der Agentenseite zeigt, wann ein Agent zuletzt etwas getan hat:
eine Aufgabe gestartet oder beendet, im Chat geantwortet, eine Nachricht ueber
einen Kanal (Telegram, Teams, Slack) bekommen. Diese Ereignisse kommen in Schueben
— ein Gespraech im Telegram-Takt waeren sonst Dutzende Schreibvorgaenge je Minute
auf dieselbe Zeile. Deshalb gedrosselt: hoechstens ein Schreibvorgang je Agent und
Minute. Die Sperre liegt in Redis, damit sie fuer alle Orchestrator-Prozesse gilt;
ohne Redis greift eine prozesslokale Sperre.

Best-effort wie ``mark_agent_interaction``: Ein Fehler hier darf weder die
Zustellung noch das Abschliessen einer Aufgabe aufhalten — schlimmstenfalls zeigt
die Liste einen aelteren Zeitpunkt.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import update

from app.core.log_redaction import scrub_log

logger = logging.getLogger(__name__)

#: Hoechstens ein Schreibvorgang je Agent in diesem Abstand.
DROSSEL_SEKUNDEN = 60

_DROSSEL_KEY = "agent:{agent_id}:last_active_drossel"

# Rueckfall ohne Redis: Agent -> monotoner Zeitpunkt des letzten Schreibens.
_lokal_zuletzt: dict[str, float] = {}


async def _darf_schreiben(redis, agent_id: str) -> bool:
    """Gibt die Minute fuer diesen Agenten frei — genau einmal je Drosselfenster."""
    client = getattr(redis, "client", None) if redis is not None else None
    if client is not None:
        try:
            frei = await client.set(
                _DROSSEL_KEY.format(agent_id=agent_id), "1", nx=True, ex=DROSSEL_SEKUNDEN,
            )
            return bool(frei)
        except Exception:  # noqa: BLE001 — dann eben prozesslokal drosseln
            logger.debug("[Aktivitaet] Redis-Drossel nicht erreichbar", exc_info=True)
    jetzt = time.monotonic()
    zuletzt = _lokal_zuletzt.get(agent_id)
    if zuletzt is not None and jetzt - zuletzt < DROSSEL_SEKUNDEN:
        return False
    _lokal_zuletzt[agent_id] = jetzt
    return True


async def aktivitaet_vermerken(redis, agent_id: str | None, now: datetime | None = None) -> bool:
    """``agents.last_active_at`` auf jetzt setzen, gedrosselt. True = geschrieben.

    ``redis`` ist ein ``RedisService`` (oder etwas mit ``.client``), darf aber auch
    ``None`` sein. Schreibt in einer eigenen Sitzung, damit der Aufrufer weder
    seine Transaktion teilen noch auf ein ``commit`` achten muss.
    """
    if not agent_id:
        return False
    if not await _darf_schreiben(redis, agent_id):
        return False
    stamp = now or datetime.now(timezone.utc)
    try:
        from app.db.session import async_session_factory
        from app.models.agent import Agent

        async with async_session_factory() as db:
            await db.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                # updated_at bleibt stehen: es bedeutet „Einstellungen geaendert“,
                # nicht „hat gearbeitet“ — sonst rueckte jeder Chat es nach vorn.
                .values(last_active_at=stamp, updated_at=Agent.updated_at)
            )
            await db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[Aktivitaet] last_active_at fuer %s nicht gespeichert", scrub_log(agent_id),
                       exc_info=True)
        return False
