"""Sekretariat und Seite „Heute“ (core/sekretariat.py)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent_manager, update_agent_integrations
from app.core.agent_manager import AgentManager
from app.db.session import get_db
from app.dependencies import is_agent_principal, require_auth, require_auth_or_agent

router = APIRouter(prefix="/sekretariat", tags=["sekretariat"])


def _fehler(e: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


async def _team(db, user):
    from app.core import sekretariat

    team = await sekretariat.von_nutzer(db, user)
    if team is None:
        raise HTTPException(status_code=404, detail="Noch kein Sekretariat eingerichtet.")
    return team


@router.get("/heute")
async def heute(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    return await sekretariat.heute(db, user)


class Einrichten(BaseModel):
    agent_ids: list[str] = Field(min_length=1, max_length=4)
    sichten_minuten: int = 30
    briefing: str = Field(default="07:30", max_length=5)


@router.post("/einrichten", status_code=201)
async def einrichten(body: Einrichten, user=Depends(require_auth), db: AsyncSession = Depends(get_db),
                     manager: AgentManager = Depends(_get_agent_manager)):
    """Sekretariat anlegen. Microsoft stellt der gewohnte Integrations-Weg ein (Rollenrechte,
    Nur-Lesen-Sperre, Neustart) — im Modus „Entwurf“: senden erst nach Freigabe."""
    from app.api.teams import _kein_betrachter, _wissen_sicherstellen
    from app.core import ms_access, sekretariat
    from app.models.agent import Agent

    _kein_betrachter(user)
    try:
        team = await sekretariat.einrichten(db, user, body.agent_ids, sichten_minuten=body.sichten_minuten,
                                            briefing=body.briefing)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    await _wissen_sicherstellen(db, team, user)

    hinweise = []
    zugang = "read" if ms_access.read_only_enabled() else ms_access.ENTWURF
    if zugang == "read":
        hinweise.append("Microsoft steht plattformweit auf Nur-Lesen: das Sekretariat liest Postfach und Kalender, "
                        "entwirft aber noch nichts. Ein Administrator löst das unter Einstellungen.")
    for agent_id in body.agent_ids:
        agent = await db.get(Agent, agent_id)
        cfg = dict(agent.config or {}) if agent else {}
        integrationen = list(dict.fromkeys([*(cfg.get("integrations") or []), "microsoft"]))
        # Immer herabstufen — auch „Lesen + Schreiben“: im Sekretariat sendet niemand ohne Freigabe.
        aenderung = {"integrations": integrationen, "msgraph_access": zugang}
        try:
            await update_agent_integrations(agent_id, aenderung, user=user, db=db, manager=manager)
        except HTTPException as e:
            hinweise.append(f"{agent.name if agent else agent_id}: Microsoft nicht eingestellt — {e.detail}")
            if agent is not None and cfg.get("msgraph_access") == "write":
                # Der gewohnte Weg scheiterte (z. B. Rollenrecht) — die Herabstufung gilt trotzdem.
                from sqlalchemy.orm.attributes import flag_modified

                cfg["msgraph_access"] = zugang
                agent.config = cfg
                flag_modified(agent, "config")
                await db.commit()
    return {"id": team.id, "hinweise": hinweise}


class Merke(BaseModel):
    text: str = Field(min_length=1, max_length=600)


@router.post("/regeln", status_code=201)
async def regel_anlegen(body: Merke, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    team = await _team(db, user)
    try:
        schluessel = await sekretariat.merken(db, team, body.text)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    return {"schluessel": schluessel, "regeln": await sekretariat.regeln(db, team)}


@router.delete("/regeln/{schluessel}")
async def regel_loeschen(schluessel: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    team = await _team(db, user)
    try:
        await sekretariat.regel_loeschen(db, team, schluessel)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    return {"regeln": await sekretariat.regeln(db, team)}


class NeueErinnerung(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    # ISO-Zeitpunkt; ohne Zeitzone gilt die der Anlage.
    faellig: datetime


async def _besitzer_des_rufers(user, db) -> tuple[str, str]:
    """(Nutzer, für den die Erinnerung ist; wer sie anlegt). Ein Agent legt sie für seinen
    Besitzer an — nur, wenn er zu dessen Sekretariat gehört."""
    from app.core import sekretariat
    from app.models.agent import Agent
    from app.models.user import User

    if not is_agent_principal(user):
        return str(user.id), "nutzer"
    agent = await db.get(Agent, str(user.id))
    besitzer = await db.get(User, agent.user_id) if agent and agent.user_id else None
    team = await sekretariat.von_nutzer(db, besitzer) if besitzer is not None else None
    if team is None or str(user.id) not in set(team.member_agent_ids or []) | {team.lead_agent_id}:
        raise HTTPException(status_code=403, detail="Erinnerungen legen nur die Agenten des Sekretariats an.")
    return str(besitzer.id), f"agent:{agent.id}"


@router.post("/erinnerungen", status_code=201)
async def erinnerung_anlegen(body: NeueErinnerung, user=Depends(require_auth_or_agent),
                             db: AsyncSession = Depends(get_db)):
    from app.core import sekretariat

    fuer, von = await _besitzer_des_rufers(user, db)
    try:
        e = await sekretariat.erinnerung_anlegen(db, fuer, body.text, body.faellig, von=von)
    except sekretariat.SekretariatFehler as e:
        raise _fehler(e)
    await db.commit()
    await db.refresh(e)
    return sekretariat.erinnerung_dict(e)


@router.post("/erinnerungen/{erinnerung_id}/erledigt")
async def erinnerung_erledigt(erinnerung_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from datetime import timezone

    from app.core import sekretariat
    from app.models.erinnerung import Erinnerung

    e = await db.get(Erinnerung, erinnerung_id)
    if e is None or e.user_id != str(user.id):
        raise HTTPException(status_code=404, detail="Erinnerung nicht gefunden.")
    e.erledigt_at = datetime.now(timezone.utc)
    await db.commit()
    return sekretariat.erinnerung_dict(e)
