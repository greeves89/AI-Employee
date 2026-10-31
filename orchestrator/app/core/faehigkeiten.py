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


#: Wie die Faehigkeiten in der Oberflaeche heissen — fuer verstaendliche Absagen.
_NAME = {c["key"]: c["label"] for c in am.CAPABILITIES}
_STAND = {am.DENY: "Verboten", am.ASK: "Freigabe", am.ALLOW: "Erlaubt"}


async def verlangen(db, agent_id: str, faehigkeit: str, *, nur_erlaubt: bool = False) -> str:
    """Abbrechen (403), wenn der Agent das nicht darf. Gibt den Zustand zurueck.

    ``nur_erlaubt``: auch „Freigabe“ reicht nicht (Handlungen, fuer die es keinen
    Freigabe-Weg gibt). Sonst sperrt nur „Verboten“.
    """
    from fastapi import HTTPException

    stand = await zustand(db, agent_id, faehigkeit)
    if stand == am.DENY or (nur_erlaubt and stand != am.ALLOW):
        raise HTTPException(status_code=403, detail=(
            f"Laut Rechte-Matrix darf dieser Agent das nicht: „{_NAME.get(faehigkeit, faehigkeit)}“ "
            f"steht auf {_STAND.get(stand, stand)}."))
    return stand


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
