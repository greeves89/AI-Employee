"""Einen schlafenden Agenten aufwecken, bevor man ihm etwas in die Warteschlange legt.

Agenten beenden sich nach einer Ruhezeit von selbst. Ihre Redis-Warteschlangen
(``agent:{id}:messages``, ``agent:{id}:chat``) werden aber **nur gelesen, solange
der Container laeuft**. Wer einem beendeten Agenten etwas hineinlegt, bekommt vom
Zustellweg ein sauberes „angenommen" — und nie eine Antwort.

Genau das ist am 2026-08-12 auf der Kundenanlage passiert: der Lead schickte
sieben Agenten „Hallo Welt", alle sieben Nachrichten stehen in der Datenbank,
keine einzige Antwort. Die Empfaenger waren Minuten vorher idle ausgestiegen. Der
Lead meldete daraufhin wahrheitsgemaess „keine Rueckmeldung" — und fuer den
Betrachter sah es aus, als koennten die Agenten nicht miteinander reden.

Fuer Besprechungen war das Aufwecken laengst geloest (``meeting_rooms``), fuer
Nachrichten zwischen Agenten nicht. Statt einer zweiten Kopie steht die Loesung
jetzt einmal hier, und beide Wege rufen sie auf.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def ensure_agent_running(agent_id: str, docker, redis) -> bool:
    """Startet den Agenten, falls sein Container nicht laeuft.

    Gibt zurueck, ob der Agent am Ende laeuft. Ist er schon wach, kostet der
    Aufruf nur eine Statusabfrage.
    """
    if not docker or not agent_id:
        return False
    try:
        from app.core.agent_manager import AgentManager
        from app.db.session import async_session_factory
        from app.models.agent import Agent

        async with async_session_factory() as db:
            agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
            if not agent:
                return False
            running = (
                bool(agent.container_id)
                and docker.get_container_status(agent.container_id) == "running"
            )
            if running:
                return True
            logger.info(
                "[Aufwecken] Agent %s schlaeft — wird fuer die Zustellung gestartet",
                agent_id,
            )
            await AgentManager(db, docker, redis).start_agent(agent_id)
            return True
    except Exception:  # noqa: BLE001
        # Ein misslungenes Aufwecken darf die Zustellung nicht abbrechen: die
        # Nachricht bleibt in der Warteschlange und wird beim naechsten Start
        # gelesen. Nur eine Antwort binnen Frist gibt es dann nicht.
        logger.warning("[Aufwecken] Agent %s konnte nicht gestartet werden",
                       agent_id, exc_info=True)
        return False
