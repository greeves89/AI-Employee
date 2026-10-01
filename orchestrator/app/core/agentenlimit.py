"""Das Agentenlimit der Lizenz — die EINE Stelle, an der es durchgesetzt wird.

Bis #886 stand die Pruefung im Endpunkt ``POST /agents``. Vier weitere Wege
legen ebenfalls Agenten an (aus Vorlage, Zuweisung durch den Administrator,
Verteilen eines trainierten Agenten, Branchenpakete) und liefen daran vorbei.
Jetzt ruft ``AgentManager.create_agent`` diese Funktion — wer einen Agenten
anlegt, kommt an ihr nicht mehr vorbei.

Durchgesetzt wird NUR das Anlegen neuer Agenten. Bestehende Agenten bleiben
unberuehrt, egal in welchem Zustand die Lizenz ist.
"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.license import get_current_license, wirksames_agentenlimit
from app.models.agent import Agent


async def agentenzahl(db: AsyncSession) -> int:
    """Alle Agenten der Anlage, laufend oder gestoppt — so zaehlt die Lizenz."""
    return (await db.execute(select(func.count(Agent.id)))).scalar() or 0


async def pruefe_agentenlimit(db: AsyncSession, zusaetzlich: int = 1) -> None:
    """Lehnt mit 402 ab, wenn ``zusaetzlich`` weitere Agenten das Limit sprengen.

    ``zusaetzlich > 1`` ist fuer Wege, die mehrere Agenten auf einmal anlegen:
    sie pruefen vorab die ganze Menge, damit kein halbes Paket entsteht.
    """
    limit, _quelle = wirksames_agentenlimit()
    if limit <= 0:
        return  # unbegrenzt
    vorhanden = await agentenzahl(db)
    if vorhanden + zusaetzlich <= limit:
        return
    lic = get_current_license()
    if zusaetzlich > 1:
        text = (f"Dafür wären {zusaetzlich} weitere Agenten nötig. Lizenziert sind {limit}, "
                f"{vorhanden} gibt es schon. Bitte Agenten löschen oder die Lizenz erweitern.")
    else:
        text = (f"Lizenz-Limit erreicht ({limit} Agenten). "
                "Bitte einen bestehenden Agenten löschen oder die Lizenz erweitern.")
    raise HTTPException(
        status_code=402,
        detail={
            "error": "agent_limit_reached",
            "message": text,
            "current_tier": lic.tier,
            "agent_limit": limit,
            "agents": vorhanden,
        },
    )
