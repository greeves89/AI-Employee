"""Der Platz eines Agenten im Büro (Agenten-Seite, Ansicht „Büro").

Rein kosmetisch wie Symbol und Farbe (``core/agent_appearance``): der Platz liegt
in ``config["buero"]`` als ``{"bereich": int, "platz": int}`` und wirkt nicht auf
das Verhalten. Vergeben wird er beim Anlegen (``AgentManager.create_agent``) —
zufällig unter den freien Plätzen der Agenten DESSELBEN Besitzers, damit jeder
sein eigenes Büro füllt. Sind alle Bereiche voll, beginnt ein neuer.

Agenten von vor dieser Funktion haben keinen Platz; die Oberfläche setzt sie
dann auf einen festen, aus der Kennung abgeleiteten freien Platz
(``frontend/src/components/raum/buero.ts``), damit sie nicht springen.
"""

from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Dieselbe Zahl steht im Frontend (components/raum/buero.ts: PLAETZE_JE_BEREICH,
# MIN_BEREICHE).
PLAETZE_JE_BEREICH = 6
MIN_BEREICHE = 4


def platz_aus(config: dict | None) -> tuple[int, int] | None:
    wert = (config or {}).get("buero")
    if not isinstance(wert, dict):
        return None
    bereich, platz = wert.get("bereich"), wert.get("platz")
    if not isinstance(bereich, int) or not isinstance(platz, int):
        return None
    if bereich < 0 or not 0 <= platz < PLAETZE_JE_BEREICH:
        return None
    return bereich, platz


def platz_waehlen(belegt: set[tuple[int, int]], zufall: random.Random | None = None) -> dict:
    """Ein zufälliger freier Platz — in den ersten Bereichen, die noch Platz haben."""
    zufall = zufall or random.SystemRandom()
    bereiche = max(MIN_BEREICHE, max((b for b, _ in belegt), default=-1) + 1)
    frei = [(b, p) for b in range(bereiche) for p in range(PLAETZE_JE_BEREICH) if (b, p) not in belegt]
    if not frei:
        frei = [(bereiche, p) for p in range(PLAETZE_JE_BEREICH)]
    bereich, platz = zufall.choice(frei)
    return {"bereich": bereich, "platz": platz}


async def freier_platz(db: AsyncSession, user_id: str | None) -> dict:
    from app.models.agent import Agent

    abfrage = select(Agent.config)
    abfrage = abfrage.where(Agent.user_id == user_id) if user_id else abfrage.where(Agent.user_id.is_(None))
    belegt = {p for cfg in (await db.execute(abfrage)).scalars().all() if (p := platz_aus(cfg))}
    return platz_waehlen(belegt)
