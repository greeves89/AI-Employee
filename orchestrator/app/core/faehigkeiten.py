"""Was ein Agent laut seiner Rechte-Matrix darf — am Server ausgewertet.

Die Matrix (``core/autonomy_matrix.py``) steuert bisher vor allem den Prompt und
den Hook im Container. Handlungen mit Wirkung nach außen entscheidet aber der
Server: Der Hook laeuft im Vertrauensbereich des Agenten, die Zugangsdaten liegen
hier. Jeder Server-Weg, der fuer einen Agenten nach außen wirkt, fragt deshalb hier
nach — und nicht in einer eigenen Kopie der Regel.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core import autonomy_matrix as am


async def anbindung_zugewiesen(db, agent_id: str, anbindung: str) -> bool:
    """Hat dieser Agent die Anbindung (``microsoft``, ``google`` …) zugewiesen?

    Der Agenten-Token allein reicht nicht: Er beweist nur, WER fragt. Ohne diese
    Pruefung laese jeder Agent eines Besitzers dessen Postfach — auch einer, dem
    der Nutzer die Anbindung nie gegeben hat.
    """
    from app.models.agent import Agent

    config = (await db.execute(select(Agent.config).where(Agent.id == agent_id))).scalar_one_or_none()
    return anbindung in ((config or {}).get("integrations") or [])


async def zustand(db, agent_id: str, faehigkeit: str) -> str:
    """``allow`` | ``ask`` | ``deny`` fuer diese Faehigkeit. Unbekannter Agent: ``deny``."""
    from app.models.agent import Agent

    zeile = (await db.execute(
        select(Agent.autonomy_level, Agent.access_policy).where(Agent.id == agent_id)
    )).first()
    if zeile is None or faehigkeit not in am.CAPABILITY_KEYS:
        return am.DENY
    stufe = (zeile[0] or "l3").lower()
    return am.normalize_matrix((zeile[1] or {}).get("autonomy_matrix"), stufe)[faehigkeit]
