"""Einrichtung eines Agenten — Status lesen, Einrichtung abschliessen.

Der Agent selbst ruft das am Ende seines Einrichtungsgespraechs auf (Werkzeug
``complete_onboarding``, in jeder Laufzeit vorhanden); die Oberflaeche liest den Status.
Fachlogik liegt in ``app.core.onboarding`` — hier nur Transport und Zugriffspruefung.

Zugriff wie ueberall: ein Agent darf ausschliesslich sich selbst einrichten, ein Nutzer
nur Agenten, die er sehen darf.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core import onboarding as ob
from app.db.session import get_db
from app.dependencies import require_auth_or_agent
from app.models.agent import Agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["onboarding"])


class OnboardingCompletion(BaseModel):
    role: str = Field(default="", max_length=500)
    boundaries: str = Field(default="", max_length=2000)
    # Jede genannte Daueraufgabe als eigener Bereich: {title, rhythm, priority, notes}
    responsibilities: list[dict] = []
    notes: str = Field(default="", max_length=1000)


async def _agent_for(agent_id: str, user, db: AsyncSession) -> Agent:
    from app.core.ownership import is_admin, visible_agent_ids
    from app.dependencies import is_agent_principal

    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if is_agent_principal(user):
        if user.id != agent_id:
            raise HTTPException(status_code=403, detail="Agent can only onboard itself")
        return agent
    if is_admin(user):
        return agent
    vids = await visible_agent_ids(user, db)
    if vids is not None and agent_id not in vids:
        raise HTTPException(status_code=403, detail="Access denied")
    return agent


@router.get("/{agent_id}/onboarding")
async def get_onboarding(
    agent_id: str,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Einrichtungsstand — vom Agenten bei jedem Sitzungsstart gelesen."""
    agent = await _agent_for(agent_id, user, db)
    return {
        "agent_id": agent_id,
        "onboarded": ob.is_onboarded(agent),
        "has_responsibilities": ob.has_duties(agent),
        "role": (agent.config or {}).get("role", ""),
    }


@router.post("/{agent_id}/onboarding/complete")
async def complete_onboarding(
    agent_id: str,
    body: OnboardingCompletion,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Einrichtung abschliessen: Rolle, Grenzen und Daueraufgaben uebernehmen.

    Die genannten Aufgaben landen als **Verantwortungsbereiche** — damit erzeugt das
    Gespraech genau die Struktur, aus der sich der Agent anschliessend seinen Tag baut.
    Ohne mindestens eine Daueraufgabe waere er zwar 'eingerichtet', haette aber weiterhin
    keinen Auftrag; deshalb wird das hier verlangt.
    """
    agent = await _agent_for(agent_id, user, db)
    if not body.responsibilities:
        raise HTTPException(
            status_code=422,
            detail="Mindestens eine Daueraufgabe angeben — sonst bleibt der Agent ohne Auftrag.",
        )
    agent.config = ob.apply_completion(
        agent,
        role=body.role,
        boundaries=body.boundaries,
        responsibilities=body.responsibilities,
        notes=body.notes,
    )
    flag_modified(agent, "config")
    await db.commit()
    duties = (agent.config.get("proactive") or {}).get("responsibilities") or []
    logger.info("[Onboarding] agent=%s abgeschlossen, %d Bereiche", agent_id, len(duties))
    return {
        "agent_id": agent_id,
        "onboarded": True,
        "responsibilities": duties,
        "role": agent.config.get("role", ""),
    }
