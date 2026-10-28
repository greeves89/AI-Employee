"""Teams API — persistent agent teams with a lead, plus task delegation."""
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tasks import _get_task_router, _get_user_agent_ids
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import get_redis_service, is_agent_principal, require_auth, require_auth_or_agent
from app.models.agent import Agent
from app.models.team import Team

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])


# Kennfarben fuer Projekte — dieselben Namen nutzt das Frontend
# (lib/projekte.ts). Eine feste Liste statt freier Werte: die Farbe landet als
# Klassenname in der Oberflaeche.
PROJEKT_FARBEN = ("blau", "violett", "gruen", "orange", "rot", "tuerkis", "gelb", "grau")


class CreateTeam(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    member_agent_ids: list[str] = []
    lead_agent_id: str | None = None
    farbe: str | None = None
    takt_minuten: int | None = None
    geteilt_mit: list[str] = []
    repo: str | None = None
    repo_secret_id: int | None = None


class UpdateTeam(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    member_agent_ids: list[str] | None = None
    lead_agent_id: str | None = None
    farbe: str | None = None
    # 0 schaltet den Takt ab; sonst einer der Werte aus projekt_takt.TAKTE.
    takt_minuten: int | None = None
    # Nutzer-IDs, die das Projekt sehen dürfen (ersetzt die bisherige Liste).
    geteilt_mit: list[str] | None = None
    # GitHub-Repo (owner/name); leer entfernt es.
    repo: str | None = None
    # Schlüssel für private Repos (Schlüsselverwaltung); 0 entfernt ihn.
    repo_secret_id: int | None = None


def _farbe(wert: str | None) -> str | None:
    if wert in (None, ""):
        return None
    if wert not in PROJEKT_FARBEN:
        raise HTTPException(status_code=422, detail=f"Unbekannte Farbe. Erlaubt: {', '.join(PROJEKT_FARBEN)}")
    return wert


def _takt(wert: int | None) -> int | None:
    from app.core.projekt_takt import TAKTE

    if not wert:
        return None
    if wert not in TAKTE:
        raise HTTPException(status_code=422, detail=f"Takt in Minuten: {', '.join(map(str, TAKTE))} oder 0 (aus).")
    return wert


def _serialize(t: Team) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "member_agent_ids": t.member_agent_ids or [],
        "lead_agent_id": t.lead_agent_id, "is_active": t.is_active,
        "created_by": t.created_by, "farbe": getattr(t, "farbe", None),
        "takt_minuten": getattr(t, "takt_minuten", None),
        "geteilt_mit": list(getattr(t, "geteilt_mit", None) or []),
        "repo": getattr(t, "repo", None),
        "repo_abgleich_at": t.repo_abgleich_at.isoformat() if getattr(t, "repo_abgleich_at", None) else None,
        "repo_fehler": getattr(t, "repo_fehler", None),
        "repo_secret_id": getattr(t, "repo_secret_id", None),
        "budget_usd": getattr(t, "budget_usd", None),
        "art": getattr(t, "art", None) or "projekt",
    }


def _fuer(t: Team, user) -> dict:
    """Wie _serialize, dazu, ob ``user`` das Projekt verwalten darf.

    ``created_by`` traegt die E-Mail des Erstellers — die gehoert nur ihm selbst
    (Freigegebene sehen fremde Adressen nicht, wie im Nutzerverzeichnis).
    """
    darf = _ist_ersteller(t, user)
    daten = _serialize(t)
    if not darf:
        daten["created_by"] = None
        daten["geteilt_mit"] = []
    return {**daten, "darf_verwalten": darf}


async def _freigabe(db: AsyncSession, ids: list[str], user) -> list[str]:
    from app.models.user import User

    ids = sorted({str(i) for i in ids if i} - {str(getattr(user, "id", ""))})
    if not ids:
        return []
    bekannt = set((await db.execute(select(User.id).where(User.id.in_(ids)))).scalars().all())
    unbekannt = set(ids) - {str(b) for b in bekannt}
    if unbekannt:
        raise HTTPException(status_code=422, detail="Unbekannter Nutzer in der Freigabe.")
    return ids


async def _projekt_nachziehen(db: AsyncSession, t: Team) -> None:
    """Zeitpläne des Teams an Lead/Mitglieder/Takt anpassen — nach jeder Änderung am Team.

    Takt (core/projekt_takt), Termine und Sekretariats-Zeitpläne (``schedules.team_id``):
    ein gelöschtes Team nimmt sie alle mit, ein neuer Lead übernimmt die des Sekretariats."""
    from sqlalchemy import delete, update

    from app.core import projekt_takt
    from app.models.schedule import Schedule

    await projekt_takt.abgleichen(db, t)
    if not t.is_active:
        await db.execute(delete(Schedule).where(Schedule.team_id == t.id))
    elif getattr(t, "art", None) == "sekretariat" and t.lead_agent_id:
        await db.execute(update(Schedule).where(
            Schedule.team_id == t.id, Schedule.name.like("[Sekretariat] %")).values(agent_id=t.lead_agent_id))
    await db.commit()


def _validate_lead(member_ids: list[str], lead_id: str | None) -> None:
    if lead_id is not None and lead_id not in (member_ids or []):
        raise HTTPException(status_code=400, detail="lead_agent_id must be one of member_agent_ids")


async def _get_team(team_id: str, db: AsyncSession) -> Team:
    t = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if not t or not t.is_active:
        raise HTTPException(status_code=404, detail="Team not found")
    return t


def _ist_ersteller(team: Team, user) -> bool:
    """Hat ``user`` das Projekt angelegt? (``created_by`` haelt E-Mail oder ID.)"""
    kennungen = {str(getattr(user, "id", "")), str(getattr(user, "email", "") or "")} - {""}
    return bool(team.created_by) and team.created_by in kennungen


async def _is_team_member(team: Team, user, db: AsyncSession) -> bool:
    """True if `user` may see this team's internals (roster/tasks/board).

    Agent principals: must literally be the lead or a member — an agent has
    no business enumerating teams it isn't part of.
    Menschen: ein Projekt sieht zunaechst NUR, wer es angelegt hat — auch kein
    Administrator und kein Besitzer eines Mitglieds-Agenten. Weitere Nutzer
    sehen es erst, wenn der Ersteller es ihnen freigibt (``geteilt_mit``).
    """
    member_ids = set(team.member_agent_ids or [])
    if team.lead_agent_id:
        member_ids.add(team.lead_agent_id)
    if is_agent_principal(user):
        return str(getattr(user, "id", "")) in member_ids
    return _ist_ersteller(team, user) or str(getattr(user, "id", "")) in set(getattr(team, "geteilt_mit", None) or [])


def _repo_setzen(t: Team, repo: str, user) -> None:
    """Repo an ein Projekt hängen (Ersteller). Gelesen wird mit dem Schlüssel des
    Projekts, anonym — oder, NUR bei Projekten von Administratoren, mit dem
    GitHub-Zugang der Anlage (core/projekt_git.zugang)."""
    from app.core.projekt_git import repo_pruefen

    try:
        neu = repo_pruefen(repo)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if neu != t.repo:
        t.repo, t.repo_abgleich_at, t.repo_fehler = neu, None, None


async def _repo_schluessel(db: AsyncSession, secret_id: int, user) -> int | None:
    """Schlüssel fürs Repo: nur einer, den ``user`` selbst nutzen darf (0 = keiner)."""
    if not secret_id:
        return None
    from app.core import secret_zugriff
    from app.models.agent_secret import AgentSecret

    secret = await db.get(AgentSecret, secret_id)
    if secret is None or not secret.is_active or await secret_zugriff.zugang(db, user, secret) is None:
        raise HTTPException(status_code=403, detail="Diesen Schlüssel darfst du nicht verwenden.")
    return secret.id


def _nur_ersteller(team: Team, user) -> None:
    """Einstellungen des Projekts (Name, Mitglieder, Lead, Takt, Freigabe, Löschen) ändert nur der Ersteller."""
    if not _ist_ersteller(team, user):
        raise HTTPException(status_code=403, detail="Nur wer das Projekt angelegt hat, kann es ändern.")


async def _require_team_access(team: Team, user, db: AsyncSession) -> None:
    if not await _is_team_member(team, user, db):
        raise HTTPException(status_code=403, detail="Not a member of this team")


async def _nur_eigene_agenten(agent_ids, user, db: AsyncSession) -> None:
    """403, wenn ein Nicht-Administrator fremde Agenten in ein Team setzen will.

    Ein Team oeffnet seinen Mitgliedern die Auftraege aller Mitglieder und laesst
    den Lead fuer sie arbeiten. Wer einen fremden Agenten neben den eigenen in
    ein Team setzen durfte, kam so an dessen Auftraege und konnte ihm Arbeit
    geben. Gemischte Teams legt deshalb nur ein Administrator an.
    """
    erlaubt = await _get_user_agent_ids(user, db)
    if erlaubt is None:
        return
    fremd = {a for a in (agent_ids or []) if a} - set(erlaubt)
    if fremd:
        raise HTTPException(status_code=403, detail="Ein Team darf nur deine eigenen Agenten enthalten.")


@router.post("/", status_code=201)
async def create_team(body: CreateTeam, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_lead(body.member_agent_ids, body.lead_agent_id)
    await _nur_eigene_agenten([*(body.member_agent_ids or []), body.lead_agent_id], user, db)
    team = Team(
        id=uuid.uuid4().hex[:32], name=body.name, description=body.description,
        member_agent_ids=body.member_agent_ids, lead_agent_id=body.lead_agent_id,
        farbe=_farbe(body.farbe), takt_minuten=_takt(body.takt_minuten),
        geteilt_mit=await _freigabe(db, body.geteilt_mit, user),
        created_by=getattr(user, "email", None) or str(getattr(user, "id", "")),
    )
    if body.repo:
        _repo_setzen(team, body.repo, user)
    if body.repo_secret_id:
        team.repo_secret_id = await _repo_schluessel(db, body.repo_secret_id, user)
    db.add(team)
    await db.commit()
    await db.refresh(team)
    await _wissen_sicherstellen(db, team, user)
    await _projekt_nachziehen(db, team)
    await db.refresh(team)
    return _fuer(team, user)


@router.get("/")
async def list_teams(user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Team).where(Team.is_active == True))).scalars().all()  # noqa: E712
    # Agent principals only enumerate teams they're actually part of (this is
    # also all the list_team_tasks MCP tool needs: scanning for "my own team").
    # Menschen ebenso: die Liste nannte bisher JEDES Team der Anlage samt den
    # Kennungen seiner Mitglieder. Administratoren sehen weiter alle
    # (_is_team_member laesst sie durch).
    rows = [t for t in rows if await _is_team_member(t, user, db)]
    return {"teams": [_serialize(t) for t in rows]}


@router.get("/mine")
async def list_my_teams(user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    """The calling agent's own teams, each with a name-resolved roster.

    Backs the `list_my_team` MCP tool. Every agent's CLAUDE.md tells leads to
    call that tool before answering "who is on my team" — but the tool only
    existed on the INBOUND MCP server (for n8n/Cursor), never in the agent's own
    tool server. Leads therefore answered from memory or from the stale
    /shared/team.json snapshot and never saw members added after their container
    started. This endpoint is the live source that fixes it.

    Must stay live (no cache): membership changes via POST /teams/{id}/members
    write only to the DB and deliberately do not restart containers.
    """
    rows = (await db.execute(select(Team).where(Team.is_active == True))).scalars().all()  # noqa: E712
    mine = [t for t in rows if await _is_team_member(t, user, db)]
    if not mine:
        return {"teams": []}

    member_ids = sorted({mid for t in mine for mid in (t.member_agent_ids or [])})
    name_by_id: dict[str, str] = {}
    role_by_id: dict[str, str | None] = {}
    if member_ids:
        agents_res = await db.execute(select(Agent).where(Agent.id.in_(member_ids)))
        for a in agents_res.scalars().all():
            name_by_id[a.id] = a.name
            role_by_id[a.id] = (a.config or {}).get("role")

    caller_id = str(getattr(user, "id", ""))
    return {
        "teams": [
            {
                "team_id": t.id,
                "name": t.name,
                "description": t.description,
                "lead_agent_id": t.lead_agent_id,
                "i_am_lead": t.lead_agent_id == caller_id,
                # Mitglieder, die es nicht mehr gibt, werden NICHT als Mitglied
                # ausgegeben. Vorher erschien die blanke Kennung als Kollege
                # „ohne Rolle" — der Lead zaehlte sie auf und haette ihr Arbeit
                # geben koennen, die nie jemand annimmt.
                "stale_member_ids": [
                    mid for mid in (t.member_agent_ids or []) if mid not in name_by_id
                ],
                "members": [
                    {
                        "id": mid,
                        "name": name_by_id.get(mid, mid),
                        "role": role_by_id.get(mid),
                        "is_lead": mid == t.lead_agent_id,
                        "is_me": mid == caller_id,
                    }
                    # Nur echte Agenten. Eine Kennung ohne Agenten ist kein
                    # Kollege, sondern ein Ueberbleibsel.
                    for mid in (t.member_agent_ids or []) if mid in name_by_id
                ],
            }
            for t in mine
        ]
    }


@router.get("/uebersicht")
async def projekte_uebersicht(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Alle Projekte des Nutzers mit den Zahlen fuer die Karten der Uebersicht."""
    from app.core import projekte

    rows = (await db.execute(select(Team).where(Team.is_active == True))).scalars().all()  # noqa: E712
    # Sichtbarkeit EINMAL bestimmen statt je Team (_is_team_member fragt sonst
    # bei jedem Team neu) — die Uebersicht laedt alle zehn Sekunden.
    sichtbar = await _get_user_agent_ids(user, db)
    ergebnis = []
    for t in rows:
        # Das Sekretariat hat seine eigene Seite (Heute) — es bleibt ein Team mit Board,
        # Gespräch und Kosten, steht aber nicht in der Projektliste.
        if getattr(t, "art", None) == "sekretariat" or not await _is_team_member(t, user, db):
            continue
        b = await projekte.board(db, t.id, sichtbar)
        ergebnis.append({
            **_fuer(t, user),
            "kennzahlen": b["kennzahlen"],
            "laufend": b["laufend"][:5],
            "naechste": b["spalten"]["bereit"][:3],
            "takt": await _takt_info(db, t),
        })
    return {"projekte": ergebnis}


@router.get("/verzeichnis")
async def projekt_freigabe_verzeichnis(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Nutzerliste für den Freigabe-Dialog eines Projekts (wie bei Apps und Workflows)."""
    from app.core.nutzer_verzeichnis import verzeichnis

    return {"users": await verzeichnis(db, user)}


@router.get("/{team_id}")
async def get_team(team_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    # Same scoping as list_teams: only teams the caller is part of.
    await _require_team_access(t, user, db)
    return _serialize(t)


@router.get("/{team_id}/tasks")
async def list_team_tasks(
    team_id: str,
    limit: int = 100,
    offset: int = 0,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    router_: TaskRouter = Depends(_get_task_router),
):
    """All tasks across every member of the team — lead + subagents.

    A team lead delegates work to its members via create_task, which creates
    a SEPARATE Task row owned by that member (agent_id = member). The lead's
    own task list (agent_id = lead) never included those — so a lead had no
    way to see what its subagents were actually doing with delegated work.
    """
    from app.models.task import Task
    from app.schemas.task import TaskResponse

    t = await _get_team(team_id, db)
    # This returns task PROMPTS/RESULTS, not just team metadata — must be
    # scoped to whoever actually belongs to the team (fixes an IDOR where any
    # authenticated user/agent in the deployment could read any team's task
    # content just by knowing/guessing its team_id).
    await _require_team_access(t, user, db)
    member_ids = t.member_agent_ids or []
    if not member_ids:
        return {"tasks": [], "total": 0, "team_id": team_id}
    tasks = await router_.list_tasks(agent_ids=member_ids, limit=limit, offset=offset)
    total = (
        await db.execute(select(func.count(Task.id)).where(Task.agent_id.in_(member_ids)))
    ).scalar() or 0
    return {
        "tasks": [TaskResponse.model_validate(task).model_dump() for task in tasks],
        "total": int(total),
        "team_id": team_id,
    }


@router.patch("/{team_id}")
async def update_team(team_id: str, body: UpdateTeam, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    await _require_team_access(t, user, db)
    _nur_ersteller(t, user)
    await _nur_eigene_agenten([*(body.member_agent_ids or []), body.lead_agent_id], user, db)
    if body.geteilt_mit is not None: t.geteilt_mit = await _freigabe(db, body.geteilt_mit, user)
    if body.repo is not None: _repo_setzen(t, body.repo, user)
    if body.repo_secret_id is not None: t.repo_secret_id = await _repo_schluessel(db, body.repo_secret_id, user)
    if body.name is not None: t.name = body.name
    if body.description is not None: t.description = body.description
    if body.member_agent_ids is not None: t.member_agent_ids = body.member_agent_ids
    if body.lead_agent_id is not None: t.lead_agent_id = body.lead_agent_id
    if body.farbe is not None: t.farbe = _farbe(body.farbe)
    if body.takt_minuten is not None: t.takt_minuten = _takt(body.takt_minuten)
    _validate_lead(t.member_agent_ids, t.lead_agent_id)
    await db.commit(); await db.refresh(t)
    await _projekt_nachziehen(db, t)
    await db.refresh(t)
    return _fuer(t, user)


@router.delete("/{team_id}")
async def delete_team(team_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    await _require_team_access(t, user, db)
    _nur_ersteller(t, user)
    t.is_active = False
    await db.commit()
    await _projekt_nachziehen(db, t)  # Takt aus
    return {"status": "deleted", "id": team_id}


class MembersChange(BaseModel):
    add: list[str] = []
    remove: list[str] = []


class SetLead(BaseModel):
    lead_agent_id: str | None


@router.post("/{team_id}/members")
async def change_members(team_id: str, body: MembersChange, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    await _require_team_access(t, user, db)
    _nur_ersteller(t, user)
    await _nur_eigene_agenten(body.add, user, db)
    members = list(t.member_agent_ids or [])
    for a in body.add:
        if a not in members:
            members.append(a)
    members = [m for m in members if m not in body.remove]
    t.member_agent_ids = members
    if t.lead_agent_id and t.lead_agent_id not in members:
        t.lead_agent_id = None  # lead removed -> clear
    await db.commit()
    await _projekt_nachziehen(db, t)
    await db.refresh(t)
    return _serialize(t)


@router.patch("/{team_id}/lead")
async def set_lead(team_id: str, body: SetLead, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    await _require_team_access(t, user, db)
    _nur_ersteller(t, user)
    await _nur_eigene_agenten([body.lead_agent_id], user, db)
    _validate_lead(t.member_agent_ids, body.lead_agent_id)
    t.lead_agent_id = body.lead_agent_id
    await db.commit()
    await _projekt_nachziehen(db, t)
    await db.refresh(t)
    return _serialize(t)


# ─── Task 4: delegate a task to a team via its lead ──────────────


async def _role_summary(agent: Agent, request: Request) -> str:
    """Best-effort first heading of the member's knowledge.md.

    Prefer the agent's CONFIGURED role/specialty (accurate + instant); only fall
    back to the knowledge.md heading if no role is set. Previously this always read
    knowledge.md, which for most agents is just the generic "Agent Knowledge Base"
    template header — so every roster entry looked identical.
    A missing role must never block delegation — keep this standalone + patchable.
    """
    role = (getattr(agent, "config", None) or {}).get("role")
    if role and str(role).strip():
        return str(role).strip()
    try:
        docker = getattr(request.app.state, "docker", None)
        if not docker or not getattr(agent, "container_id", None):
            return ""
        _, content = docker.exec_in_container(agent.container_id, "cat /workspace/knowledge.md")
        for line in (content or "").splitlines():
            line = line.strip()
            if not line:
                continue
            line = line.lstrip("#").strip()  # drop leading heading markers
            if line.lower().startswith("rolle:"):
                line = line[len("rolle:"):].strip()
            return line
    except Exception:
        return ""
    return ""


async def _team_roster(team: Team, db: AsyncSession, request: Request) -> str:
    """Render the team's members as a roster string (one line per member)."""
    lines: list[str] = []
    for aid in (team.member_agent_ids or []):
        agent = (await db.execute(select(Agent).where(Agent.id == aid))).scalar_one_or_none()
        if not agent:
            continue
        role = await _role_summary(agent, request)
        suffix = " (LEAD)" if aid == team.lead_agent_id else ""
        lines.append(f"- {agent.name} [{aid}]{suffix}: {role}")
    return "\n".join(lines)


LEAD_INSTRUCTION = (
    "Du bist der LEAD dieses Teams. Zerlege die Aufgabe und delegiere sie via "
    "`create_task` an die passende(n) Rolle(n) im Roster. Warte auf ihre Ergebnisse "
    "(sie kommen als Delegation-Result zurück), konsolidiere und melde das Gesamtergebnis. "
    "Passt keine Rolle, sag das ehrlich — rate nicht.\n\n## Team-Roster\n{roster}\n"
)


class DelegateTask(BaseModel):
    title: str
    prompt: str
    priority: int = 5


async def _darf_beauftragen(agent_id: str, user, db: AsyncSession) -> None:
    """403, wenn ``user`` diesem Agenten keine Arbeit geben darf.

    Teamzugang allein reicht nicht: gemischte Teams (nur von Administratoren
    angelegt) enthalten Agenten mehrerer Nutzer. Wer nur EINEN Agenten im Team
    hat, darf deshalb nicht den Lead oder ein Mitglied eines anderen Nutzers
    beauftragen.
    """
    erlaubt = await _sicht(user, db)
    if erlaubt is not None and agent_id not in erlaubt:
        raise HTTPException(status_code=403, detail="Diesem Agenten kannst du keine Aufträge geben.")


async def _an_lead(t: Team, title: str, prompt: str, priority: int, request: Request,
                   router_: TaskRouter, db: AsyncSession, user, parent_task_id: str | None = None, **meta):
    """Auftrag an den Lead des Teams — mit Anleitung und Roster davor."""
    if not t.lead_agent_id:
        raise HTTPException(status_code=400, detail="assign a lead first")
    if not (t.member_agent_ids or []):
        raise HTTPException(status_code=400, detail="team has no members")
    await _darf_beauftragen(t.lead_agent_id, user, db)
    roster = await _team_roster(t, db, request)
    prefix = LEAD_INSTRUCTION.format(roster=roster)
    return await router_.create_and_route_task(
        title=title, prompt=prefix + "\n## Aufgabe\n" + prompt,
        priority=priority, agent_id=t.lead_agent_id, parent_task_id=parent_task_id,
        # "auftrag": der Text, wie der Mensch ihn erteilt hat.
        metadata={"team_id": t.id, "delegated_to_team": True, "auftrag": prompt, **meta},
    )


@router.post("/{team_id}/tasks", status_code=201)
async def delegate_to_team(team_id: str, body: DelegateTask, request: Request,
                           router_: TaskRouter = Depends(_get_task_router),
                           user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Auftrag ans Team (Teamansicht der Agentenseite) — als Karte auf dem Projekt-Board."""
    from app.core import projekte

    _kein_betrachter(user)
    t = await _get_team(team_id, db)
    await _require_team_access(t, user, db)
    if not t.lead_agent_id:
        raise HTTPException(status_code=400, detail="assign a lead first")
    karte = projekte.neue_karte(t.id, body.title, body.prompt, spalte="bereit", von=_wer(user),
                                ersteller_user_id=await _besitzer(user, db))
    db.add(karte)
    await db.commit()
    karte = await _vergeben(t, karte.id, None, user, request, router_, db)
    return {"task_id": karte["task_id"], "lead_agent_id": t.lead_agent_id, "status": karte["task_status"],
            "karte_id": karte["id"]}


# ─── Projekte: Board aus Karten, Vergeben, Abnahme, Wissen ───────
#
# Menschen UND Agenten des Projekts arbeiten hier (require_auth_or_agent +
# _require_team_access: ein Agent muss Mitglied oder Lead sein). Abnehmen und
# Zurückgeben bleiben Menschen vorbehalten.


def _kein_betrachter(user) -> None:
    from app.models.user import UserRole

    if getattr(user, "role", None) == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Betrachter können im Projekt nichts ändern.")


def _wer(user) -> str:
    if is_agent_principal(user):
        return f"agent:{user.id}"
    return getattr(user, "email", None) or str(getattr(user, "id", ""))


async def _sicht(user, db: AsyncSession) -> list[str] | None:
    """Agenten, deren Läufe ``user`` sehen und beauftragen darf (None = alle).

    Ein Agent steht für seinen Besitzer: er sieht und beauftragt die Agenten
    desselben Nutzers — in einem gemischten Projekt nicht die der anderen.
    """
    if is_agent_principal(user):
        ich = await db.get(Agent, str(user.id))
        if ich is None or not ich.user_id:
            return [str(user.id)]
        return list((await db.execute(select(Agent.id).where(Agent.user_id == ich.user_id))).scalars().all())
    return await _get_user_agent_ids(user, db)


async def _besitzer(user, db: AsyncSession) -> str | None:
    """Der Nutzer hinter ``user`` — bei einem Agenten sein Besitzer."""
    if is_agent_principal(user):
        ich = await db.get(Agent, str(user.id))
        return ich.user_id if ich else None
    return str(getattr(user, "id", "")) or None


async def _fremd_fuer(karte, agent_id: str | None, db: AsyncSession) -> bool:
    """Stammt die Karte von einem anderen Nutzer als dem Besitzer des Ziel-Agenten?

    Unbekannter Ersteller (z. B. ein Plattform-Agent ohne Besitzer) gilt als fremd:
    im Zweifel ist der Text Material, nicht Anweisung.
    """
    if not agent_id:
        return True
    ziel = await db.get(Agent, agent_id)
    return ziel is None or not karte.ersteller_user_id or ziel.user_id != karte.ersteller_user_id


async def _projekt(team_id: str, user, db: AsyncSession) -> Team:
    t = await _get_team(team_id, db)
    await _require_team_access(t, user, db)
    return t


async def _takt_info(db: AsyncSession, t: Team) -> dict:
    from app.models.schedule import Schedule

    sched = await db.get(Schedule, t.takt_schedule_id) if t.takt_schedule_id else None
    an = bool(t.takt_minuten and sched is not None and sched.enabled)
    return {
        "minuten": t.takt_minuten,
        "aktiv": an,
        "naechster_lauf": sched.next_run_at.isoformat() if an and sched.next_run_at else None,
        "agent_id": sched.agent_id if an else None,
    }


async def _wissen_sicherstellen(db: AsyncSession, t: Team, user) -> None:
    from app.core import projekt_wissen

    try:
        await projekt_wissen.sicherstellen(db, t, str(getattr(user, "id", "")) or None)
    except Exception as e:  # noqa: BLE001 — ein Projekt entsteht auch ohne Wissensablage
        await db.rollback()
        logger.warning("Projektwissen für %s nicht angelegt: %s", t.id, e)


def _kartenfehler(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(status_code=404, detail=str(e) or "Karte nicht gefunden.")
    return HTTPException(status_code=409, detail=str(e))


@router.get("/{team_id}/board")
async def projekt_board(team_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import projekte

    t = await _projekt(team_id, user, db)
    if not t.wissen_slug and not is_agent_principal(user):
        await _wissen_sicherstellen(db, t, user)
    return {
        "projekt": _serialize(t) if is_agent_principal(user) else _fuer(t, user),
        "takt": await _takt_info(db, t),
        **(await projekte.board(db, t.id, await _sicht(user, db))),
    }


@router.get("/{team_id}/board/text")
async def projekt_board_text(team_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    """Das Board als Text — für die Werkzeuge der Agenten (projekt_board)."""
    from app.core import projekte

    t = await _projekt(team_id, user, db)
    b = await projekte.board(db, t.id, await _sicht(user, db))
    ids = set(t.member_agent_ids or []) | {k.get("agent_id") for s in b["spalten"].values() for k in s} \
        | {lauf["agent_id"] for lauf in b["laufend"]}
    ids.discard(None)
    namen = {a.id: a.name for a in (await db.execute(select(Agent).where(Agent.id.in_(ids)))).scalars()} if ids else {}
    mitglieder = ", ".join(
        namen.get(m, m) + (" (Lead)" if m == t.lead_agent_id else "") for m in (t.member_agent_ids or [])
    )
    text = projekte.board_als_text(_serialize(t), b, namen)
    return {"projekt_id": t.id, "text": f"{text}\n\nMitglieder: {mitglieder or '—'}"}


class NeueKarte(BaseModel):
    titel: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=20000)
    spalte: str = "eingang"
    prioritaet: int = Field(default=2, ge=1, le=3)


@router.post("/{team_id}/karten", status_code=201)
async def karte_anlegen(team_id: str, body: NeueKarte, user=Depends(require_auth_or_agent),
                        db: AsyncSession = Depends(get_db)):
    from app.core import projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    try:
        karte = projekte.neue_karte(t.id, body.titel, body.text, spalte=body.spalte,
                                    prioritaet=body.prioritaet, von=_wer(user),
                                    ersteller_user_id=await _besitzer(user, db))
    except projekte.KartenFehler as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.add(karte)
    await db.commit()
    return projekte.karte_dict(karte)


class KarteAendern(BaseModel):
    spalte: str | None = None
    prioritaet: int | None = Field(default=None, ge=1, le=3)
    reihenfolge: float | None = None
    notiz: str | None = Field(default=None, max_length=500)
    titel: str | None = Field(default=None, min_length=1, max_length=300)
    text: str | None = Field(default=None, max_length=20000)


@router.patch("/{team_id}/karten/{karte_id}")
async def karte_aendern(team_id: str, karte_id: str, body: KarteAendern, user=Depends(require_auth_or_agent),
                        db: AsyncSession = Depends(get_db)):
    from app.core import projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    try:
        karte = await projekte.karte_laden(db, t.id, karte_id, sperren=True)
        projekte.verschieben(karte, spalte=body.spalte, prioritaet=body.prioritaet,
                             reihenfolge=body.reihenfolge, notiz=body.notiz)
    except (LookupError, projekte.KartenFehler) as e:
        await db.rollback()
        raise _kartenfehler(e)
    # Titel und Text: nur eigene Karten (Issues kommen aus dem Repo), nur im Eingang
    # (danach ist sie eingeplant oder vergeben) und nur vom Nutzer, dem die Karte
    # gehoert — sonst liesse sich ein Auftrag an fremde Agenten unterschieben.
    if body.titel is not None or body.text is not None:
        # Auch fuer Administratoren: sonst liesse sich die Rahmung fremder Karten umgehen.
        if karte.quelle != "eigen" or karte.spalte != "eingang" or (
            karte.ersteller_user_id != await _besitzer(user, db)
        ):
            await db.rollback()
            raise HTTPException(status_code=409, detail="Titel und Text ändert nur, wem die Karte gehört — und nur im Eingang.")
        if body.titel is not None:
            karte.titel = body.titel.strip()
        if body.text is not None:
            karte.text = body.text.strip()
    await db.commit()
    return projekte.karte_dict(karte)


class Vergeben(BaseModel):
    # Leer: Agent ruft → er selbst; Mensch ruft → der Lead (oder das einzige Mitglied).
    agent_id: str | None = None


async def _vergeben(t: Team, karte_id: str, agent_id: str | None, user, request: Request,
                    router_: TaskRouter, db: AsyncSession) -> dict:
    """Karte an einen Agenten geben: legt den Lauf an und schiebt sie nach In Arbeit."""
    from app.core import projekte

    mitglieder = set(t.member_agent_ids or []) | ({t.lead_agent_id} if t.lead_agent_id else set())
    ziel = agent_id or (str(user.id) if is_agent_principal(user) else None)
    an_lead = False
    if not ziel:
        if t.lead_agent_id:
            an_lead = True
        elif len(t.member_agent_ids or []) == 1:
            ziel = t.member_agent_ids[0]
        else:
            raise HTTPException(status_code=400, detail="Wähle einen Agenten aus oder bestimme einen Lead für das Projekt.")
    if ziel:
        if ziel not in mitglieder:
            raise HTTPException(status_code=400, detail="Dieser Agent gehört nicht zum Projekt.")
        await _darf_beauftragen(ziel, user, db)

    # Erst unter Sperre als „in Arbeit“ markieren, dann den Lauf anlegen: der Router
    # committet in derselben Sitzung (Sperre weg) — ein Doppelklick legte sonst zwei an.
    try:
        karte = await projekte.karte_laden(db, t.id, karte_id, sperren=True)
    except LookupError as e:
        raise _kartenfehler(e)
    if karte.spalte not in ("eingang", "bereit"):
        await db.rollback()
        raise HTTPException(status_code=409, detail="Nur Karten aus Eingang oder Bereit lassen sich vergeben.")
    vorher = karte.spalte
    team_id, lead_id = t.id, t.lead_agent_id
    karte.spalte = "in_arbeit"
    karte.task_id = None
    await db.commit()

    prioritaet = {1: 2, 2: 1, 3: 0}.get(karte.prioritaet, 1)
    fremd = await _fremd_fuer(karte, ziel or t.lead_agent_id, db)
    auftrag = projekte.karten_auftrag(karte, t.name, fremd=fremd)
    titel = projekte.lauf_titel(karte, fremd)
    try:
        if an_lead:
            task = await _an_lead(t, titel, auftrag, prioritaet, request, router_, db, user,
                                  karte_id=karte.id)
        else:
            task = await router_.create_and_route_task(
                title=titel, prompt=auftrag, priority=prioritaet, agent_id=ziel,
                metadata={"team_id": t.id, "karte_id": karte.id, "auftrag": karte.text or karte.titel},
            )
    except Exception:
        # Nach dem Rollback sind geladene Objekte verfallen — nur gemerkte Werte nutzen.
        await db.rollback()
        karte = await projekte.karte_laden(db, team_id, karte_id)
        karte.spalte = vorher
        await db.commit()
        raise
    karte = await projekte.karte_laden(db, team_id, karte_id)
    projekte.in_arbeit_setzen(karte, task, ziel or lead_id)
    await db.commit()
    return projekte.karte_dict(karte, task)


@router.post("/{team_id}/karten/{karte_id}/vergeben", status_code=201)
async def karte_vergeben(team_id: str, karte_id: str, body: Vergeben, request: Request,
                         router_: TaskRouter = Depends(_get_task_router),
                         user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    return await _vergeben(t, karte_id, body.agent_id, user, request, router_, db)


class Stand(BaseModel):
    stufe: str | None = None
    pr_url: str | None = Field(default=None, max_length=500)
    notiz: str | None = Field(default=None, max_length=500)


@router.post("/{team_id}/karten/{karte_id}/stand")
async def karte_stand(team_id: str, karte_id: str, body: Stand, user=Depends(require_auth_or_agent),
                      db: AsyncSession = Depends(get_db)):
    from app.core import projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    try:
        karte = await projekte.karte_laden(db, t.id, karte_id, sperren=True)
        projekte.stand_melden(karte, stufe=body.stufe, pr_url=body.pr_url, notiz=body.notiz)
    except (LookupError, projekte.KartenFehler) as e:
        await db.rollback()
        raise _kartenfehler(e)
    await db.commit()
    return projekte.karte_dict(karte)


async def _lauf_der_karte(db: AsyncSession, karte, user):
    """Der Lauf zur Karte — nur, wenn ``user`` ihn sehen darf (sonst kein Ergebnis/keine Nacharbeit)."""
    from app.models.task import Task

    if not karte.task_id:
        return None
    task = await db.get(Task, karte.task_id)
    sicht = await _sicht(user, db)
    if task is not None and sicht is not None and task.agent_id not in sicht:
        raise HTTPException(status_code=403, detail="Diesen Lauf darfst du nicht abnehmen.")
    return task


@router.post("/{team_id}/karten/{karte_id}/abnehmen")
async def karte_abnehmen(team_id: str, karte_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    try:
        karte = await projekte.karte_laden(db, t.id, karte_id, sperren=True)
        task = await _lauf_der_karte(db, karte, user)
        projekte.abnehmen(karte, task)
    except (LookupError, projekte.KartenFehler) as e:
        await db.rollback()
        raise _kartenfehler(e)
    antwort = projekte.karte_dict(karte)
    await db.commit()
    await _ergebnis_festhalten(db, t, antwort, task, user)
    return antwort


async def _ergebnis_festhalten(db: AsyncSession, t: Team, karte: dict, task, user) -> None:
    """Abgenommenes Ergebnis ins Projektwissen (ergebnisse.md) und ins Gespräch.

    So wächst das Wissen mit, ohne dass ein Agent daran denken muss. Best effort —
    die Abnahme selbst ist schon gespeichert."""
    from datetime import datetime

    from app.core import projekt_gespraech, projekt_wissen

    try:
        team_id, team_name = t.id, t.name
        agent = await db.get(Agent, task.agent_id) if task is not None and task.agent_id else None
        ergebnis = ((task.result if task is not None else "") or "").strip()
        if len(ergebnis) > 2000:
            ergebnis = ergebnis[:2000].rstrip() + "\n[… gekürzt]"
        nr = f" (#{karte['extern_nr']})" if karte.get("extern_nr") else ""
        abschnitt = (f"## {datetime.now().strftime('%d.%m.%Y')} — {karte['titel']}{nr}\n"
                     f"Abgenommen; bearbeitet von {agent.name if agent else '—'}.\n\n{ergebnis or '(ohne Ergebnistext)'}")
        brain = await projekt_wissen.sicherstellen(db, t)
        await projekt_wissen.anhaengen(db, brain, "ergebnisse.md", abschnitt, wer=getattr(user, "email", None) or "Abnahme",
                                       kopf=f"Ergebnisse — {team_name}")
        t = await db.get(Team, team_id)
        await projekt_gespraech.posten(db, t, autor_typ="system", autor_id=None, autor_name="Abnahme",
                                       text=f"Abgenommen: {karte['titel']}{nr} — steht in ergebnisse.md.", art="ergebnis",
                                       karte_id=karte["id"])
        await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.warning("Ergebnis von Karte %s nicht festgehalten: %s", karte.get("id"), e)


class Zurueckgeben(BaseModel):
    hinweis: str = Field(min_length=1, max_length=8000)
    # Leer: an den bisherigen Bearbeiter, solange er im Projekt ist — sonst an den Lead.
    # Sonst direkt an dieses Mitglied (oder den Lead).
    agent_id: str | None = None


@router.post("/{team_id}/karten/{karte_id}/zurueckgeben", status_code=201)
async def karte_zurueckgeben(team_id: str, karte_id: str, body: Zurueckgeben, request: Request,
                             router_: TaskRouter = Depends(_get_task_router),
                             user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Ergebnis nicht abnehmen, sondern mit Hinweis zur Nacharbeit zurückgeben."""
    from app.core import projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    try:
        karte = await projekte.karte_laden(db, t.id, karte_id, sperren=True)
    except LookupError as e:
        raise _kartenfehler(e)
    if karte.spalte != "pruefung":
        await db.rollback()
        raise HTTPException(status_code=409, detail="Zurückgeben geht nur in der Prüfung.")
    alt = await _lauf_der_karte(db, karte, user)
    team_id, lead_id = t.id, t.lead_agent_id
    mitglieder = set(t.member_agent_ids or []) | ({lead_id} if lead_id else set())
    if body.agent_id:
        if body.agent_id not in mitglieder:
            await db.rollback()
            raise HTTPException(status_code=400, detail="Dieser Agent gehört nicht zum Projekt.")
        ueber_lead, ziel = False, body.agent_id
    else:
        # Der bisherige Bearbeiter — ist er nicht mehr im Projekt (herausgenommen,
        # am Limit), entscheidet der Lead, wer nacharbeitet.
        bisher = karte.agent_id or (alt.agent_id if alt else None)
        ueber_lead = bool(lead_id and (
            bisher not in mitglieder or (alt is not None and (alt.metadata_ or {}).get("delegated_to_team"))))
        ziel = None if ueber_lead else bisher
        if not ueber_lead and ziel not in mitglieder:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Der bisherige Agent ist nicht mehr im Projekt — wähle, wer nacharbeitet.")
    karte.spalte = "in_arbeit"  # Sperre: ein Doppelklick legt keine zweite Nacharbeit an
    await db.commit()

    fremd = await _fremd_fuer(karte, ziel or t.lead_agent_id, db)
    prompt = projekte.nacharbeit_prompt(karte, alt, body.hinweis, t.name, fremd=fremd)
    titel = f"Nacharbeit: {projekte.lauf_titel(karte, fremd)}"[:200]
    try:
        if ueber_lead:
            neu = await _an_lead(t, titel, prompt, 1, request, router_, db, user,
                                 parent_task_id=alt.id if alt else None, karte_id=karte.id)
        else:
            await _darf_beauftragen(ziel, user, db)
            neu = await router_.create_and_route_task(
                title=titel, prompt=prompt, priority=1, agent_id=ziel,
                parent_task_id=alt.id if alt else None,
                metadata={"team_id": t.id, "karte_id": karte.id, "auftrag": karte.text or karte.titel},
            )
    except Exception:
        await db.rollback()
        karte = await projekte.karte_laden(db, team_id, karte_id)
        karte.spalte = "pruefung"
        await db.commit()
        raise
    karte = await projekte.karte_laden(db, team_id, karte_id)
    projekte.in_arbeit_setzen(karte, neu, ziel or lead_id)
    await db.commit()
    return projekte.karte_dict(karte, neu)


class ProjektAuftrag(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    titel: str | None = Field(default=None, max_length=300)
    # Leer: an den Lead (oder das einzige Mitglied). Sonst direkt an dieses Mitglied.
    agent_id: str | None = None
    # Nur als Karte in den Eingang legen, nicht gleich vergeben.
    nur_eingang: bool = False
    prioritaet: int = Field(default=2, ge=1, le=3)


@router.post("/{team_id}/auftrag", status_code=201)
async def projekt_auftrag(team_id: str, body: ProjektAuftrag, request: Request,
                          router_: TaskRouter = Depends(_get_task_router),
                          user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Auftrag ans Projekt — per Eingabezeile oder Klick im Raum: eine Karte, gleich vergeben."""
    from app.core import projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Der Auftrag ist leer.")
    titel = (body.titel or "").strip() or projekte.titel_aus(text)
    karte = projekte.neue_karte(t.id, titel, text, spalte="eingang" if body.nur_eingang else "bereit",
                                prioritaet=body.prioritaet, von=_wer(user), ersteller_user_id=await _besitzer(user, db))
    db.add(karte)
    await db.commit()
    if body.nur_eingang:
        return projekte.karte_dict(karte)
    return await _vergeben(t, karte.id, body.agent_id, user, request, router_, db)


# ─── Projektwissen ──────────────────────────────────────────────


async def _projekt_brain(t: Team, user, db: AsyncSession):
    from app.core import projekt_wissen

    brain = await projekt_wissen.brain_des_projekts(db, t)
    if brain is None:
        brain = await projekt_wissen.sicherstellen(db, t, str(getattr(user, "id", "")) or None)
    return brain


def _keine_dateiseite(pfad: str, *, lesen: bool = False) -> None:
    """Seiten unter ``dateien/`` gehören den hochgeladenen Dateien.

    Schreiben: 400 (sonst ersetzte eine Hand-Seite den ausgelesenen Text oder
    umgekehrt). Verlauf: 404 — gelöschte Dateien sollen dort nicht weiterleben.
    """
    from app.core.projekt_dateien import ist_dateiseite

    if ist_dateiseite(pfad):
        if lesen:
            raise HTTPException(status_code=404, detail="Für hochgeladene Dateien gibt es keinen Verlauf.")
        raise HTTPException(status_code=400, detail="„dateien/“ ist für hochgeladene Dateien reserviert — bitte einen anderen Pfad wählen.")


@router.get("/{team_id}/wissen")
async def wissen_liste(team_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import vault

    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    eintraege = [e for e in vault.list_entries(brain.host_path) if not e["name"].startswith(".")]
    return {"projekt_id": t.id, "dateien": eintraege}


@router.get("/{team_id}/wissen/datei")
async def wissen_lesen(team_id: str, pfad: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import vault

    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    try:
        return {"pfad": pfad, "inhalt": vault.read_file(brain.host_path, pfad)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Seite nicht gefunden.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class WissenSeite(BaseModel):
    pfad: str = Field(min_length=1, max_length=300)
    inhalt: str = Field(max_length=200_000)


@router.put("/{team_id}/wissen/datei")
async def wissen_schreiben(team_id: str, body: WissenSeite, user=Depends(require_auth_or_agent),
                           db: AsyncSession = Depends(get_db)):
    from app.core import projekt_wissen

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    try:
        _keine_dateiseite(body.pfad)
        return await projekt_wissen.schreiben(db, brain, body.pfad.strip(), body.inhalt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── Projektdateien (core/projekt_dateien) ─────────────────────


@router.get("/{team_id}/wissen/dateien")
async def dateien_liste(team_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import projekt_dateien

    t = await _projekt(team_id, user, db)
    return {"projekt_id": t.id, "dateien": projekt_dateien.liste(await _projekt_brain(t, user, db)),
            "max_bytes": projekt_dateien.MAX_BYTES, "endungen": sorted(projekt_dateien.ENDUNGEN)}


@router.post("/{team_id}/wissen/dateien", status_code=201)
async def datei_hochladen(team_id: str, datei: UploadFile = File(...), user=Depends(require_auth),
                          db: AsyncSession = Depends(get_db)):
    """Datei ans Projekt hängen: Original zum Herunterladen, Text ins Projektwissen."""
    from app.core import projekt_dateien

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    # Nicht mehr lesen als erlaubt — eine riesige Datei soll nicht erst ganz im Speicher landen.
    inhalt = await datei.read(projekt_dateien.MAX_BYTES + 1)
    if len(inhalt) > projekt_dateien.MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Die Datei ist größer als {projekt_dateien.MAX_BYTES // 1024 // 1024} MB.")
    try:
        return await projekt_dateien.ablegen(db, brain, datei.filename or "", inhalt, wer=_wer(user))
    except projekt_dateien.DateiFehler as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{team_id}/wissen/dateien/{name}")
async def datei_herunterladen(team_id: str, name: str, user=Depends(require_auth_or_agent),
                              db: AsyncSession = Depends(get_db)):
    from fastapi.responses import FileResponse

    from app.core import projekt_dateien

    t = await _projekt(team_id, user, db)
    try:
        pfad = projekt_dateien.pfad_zum_herunterladen(await _projekt_brain(t, user, db), name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
    # Immer als Download, nie im Browser dargestellt — die Datei kommt von Nutzern.
    return FileResponse(pfad, filename=os.path.basename(pfad), media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/{team_id}/wissen/dateien/{name}", status_code=204)
async def datei_loeschen(team_id: str, name: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from app.core import projekt_dateien

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    try:
        await projekt_dateien.loeschen(db, await _projekt_brain(t, user, db), name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")


@router.get("/{team_id}/wissen/suche")
async def wissen_suchen(team_id: str, frage: str, user=Depends(require_auth_or_agent),
                        db: AsyncSession = Depends(get_db)):
    from app.core import projekt_wissen

    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    frage = frage.strip()[:500]
    if not frage:
        raise HTTPException(status_code=422, detail="Die Suchfrage ist leer.")
    return {"projekt_id": t.id, "treffer": await projekt_wissen.suchen(db, brain, frage)}


@router.post("/{team_id}/repo/abgleichen")
async def repo_abgleichen(team_id: str, beim_oeffnen: bool = False, user=Depends(require_auth),
                          db: AsyncSession = Depends(get_db), redis=Depends(get_redis_service)):
    """Issues und PRs des Repos sofort aufs Board holen (sonst alle zehn Minuten).

    ``beim_oeffnen``: von der Projektseite beim Aufruf — gebremst (siehe
    ``projekt_git.beim_oeffnen``) und still: Fehler stehen am Projekt, nicht im Aufruf.
    """
    from app.core import projekt_git

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    if not t.repo:
        raise HTTPException(status_code=400, detail="Das Projekt hat kein Repo.")
    if beim_oeffnen:
        try:
            ergebnis = await projekt_git.beim_oeffnen(db, redis, t)
        except projekt_git.GitFehler:
            ergebnis = None
        except Exception as e:  # noqa: BLE001 — z. B. gleichzeitiger Takt-Abgleich derselben Karten
            await db.rollback()
            logger.info("Abgleich beim Öffnen %s übersprungen: %s", team_id, e)
            ergebnis = None
        return {"uebersprungen": True} if ergebnis is None else {**ergebnis, "uebersprungen": False}
    try:
        return await projekt_git.projekt_abgleichen(db, redis, t)
    except projekt_git.GitFehler as e:
        # 424 statt 502: Cloudflare ersetzt jede 502-Antwort durch seine eigene
        # Fehlerseite — die Meldung („dem Schlüssel fehlt …“) kam nie an.
        raise HTTPException(status_code=424, detail=str(e))


# ─── Projektgespräch ───────────────────────────────────────────


async def _autor(user, db: AsyncSession) -> tuple[str, str | None, str]:
    """(autor_typ, autor_id, Anzeigename) des Aufrufers."""
    if is_agent_principal(user):
        ich = await db.get(Agent, str(user.id))
        return "agent", str(user.id), ich.name if ich else "Agent"
    return "mensch", str(getattr(user, "id", "")), getattr(user, "name", None) or getattr(user, "email", None) or "Mensch"


@router.get("/{team_id}/gespraech")
async def gespraech_lesen(team_id: str, limit: int = 100, vor_id: int | None = None,
                          user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import projekt_gespraech

    t = await _projekt(team_id, user, db)
    nachrichten = await projekt_gespraech.liste(db, t.id, limit=limit, vor_id=vor_id)
    return {"projekt_id": t.id, "nachrichten": [projekt_gespraech.nachricht_dict(n) for n in nachrichten]}


class GespraechNachricht(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    art: str = "nachricht"
    # Angesprochene Agenten (IDs). Zusätzlich wirkt „@Name“ im Text.
    an: list[str] = []
    karte_id: str | None = None


@router.post("/{team_id}/gespraech", status_code=201)
async def gespraech_schreiben(team_id: str, body: GespraechNachricht,
                              router_: TaskRouter = Depends(_get_task_router),
                              user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    """Ins Projektgespräch schreiben. Angesprochene Agenten antworten in einem eigenen Lauf;
    schreibt ein Mensch niemanden an, antwortet der Lead."""
    from app.core import projekt_gespraech, projekt_wissen

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    typ, autor_id, name = await _autor(user, db)
    namen = await projekt_gespraech.mitglieder_namen(db, t)
    an = list(dict.fromkeys([*body.an, *projekt_gespraech.angesprochen(body.text, namen)]))
    if not an and typ == "mensch" and t.lead_agent_id and body.art in ("nachricht", "frage"):
        an = [t.lead_agent_id]
    sicht = await _sicht(user, db)
    try:
        nachricht = await projekt_gespraech.posten(
            db, t, autor_typ=typ, autor_id=autor_id, autor_name=name, text=body.text, art=body.art, an=an,
            karte_id=body.karte_id, darf_wecken=None if sicht is None else set(sicht),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await db.commit()
    await db.refresh(nachricht)
    if body.art == "entscheidung":
        try:
            brain = await projekt_wissen.sicherstellen(db, t)
            await projekt_wissen.anhaengen(db, brain, "entscheidungen.md",
                                           f"## {nachricht.created_at:%d.%m.%Y} — {name}\n{body.text.strip()}",
                                           wer=name, kopf=f"Entscheidungen — {t.name}")
        except Exception as e:  # noqa: BLE001
            logger.warning("Entscheidung nicht ins Wissen geschrieben: %s", e)
    # Wer angesprochen ist, wird im Hintergrund geweckt — "geweckt" nennt, wer gleich antwortet.
    geweckt = list(nachricht.an or []) if nachricht.an and await projekt_gespraech.weck_grenze_frei(db, t.id) else []
    antwort = {**projekt_gespraech.nachricht_dict(nachricht), "geweckt": geweckt}
    if geweckt:
        projekt_gespraech.im_hintergrund_wecken(router_, t.id, nachricht.id)
    return antwort


# ─── Termine ───────────────────────────────────────────────────


def _darf_termine(t: Team, user) -> None:
    """Termine legt der Ersteller des Projekts an — oder sein Lead (selbständige Abstimmung)."""
    if is_agent_principal(user):
        if str(user.id) != t.lead_agent_id:
            raise HTTPException(status_code=403, detail="Termine legt der Lead des Projekts an.")
        return
    _nur_ersteller(t, user)


@router.get("/{team_id}/termine")
async def termine_liste(team_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import projekt_termine
    from app.models.meeting_room import MeetingRoom

    t = await _projekt(team_id, user, db)
    raeume = (await db.execute(
        select(MeetingRoom).where(MeetingRoom.team_id == t.id).order_by(MeetingRoom.created_at.desc()).limit(20)
    )).scalars().all()

    def _zusammenfassung(r):
        for m in reversed(r.messages or []):
            if m.get("role") == "summary":
                return (m.get("content") or "")[:600]
        return None

    return {
        "projekt_id": t.id,
        "termine": [projekt_termine.termin_dict(s) for s in await projekt_termine.liste(db, t.id)],
        "besprechungen": [
            {"id": r.id, "name": r.name, "zustand": r.state, "erstellt": r.created_at.isoformat() if r.created_at else None,
             "zusammenfassung": _zusammenfassung(r)}
            for r in raeume
        ],
    }


class NeuerTermin(BaseModel):
    art: str  # daily | weekly | einmalig
    uhrzeit: str = Field(min_length=4, max_length=5)
    wochentag: str | None = Field(default=None, max_length=10)
    datum: str | None = Field(default=None, max_length=10)
    thema: str | None = Field(default=None, max_length=300)


@router.post("/{team_id}/termine", status_code=201)
async def termin_anlegen(team_id: str, body: NeuerTermin, user=Depends(require_auth_or_agent),
                         db: AsyncSession = Depends(get_db)):
    from app.core import projekt_gespraech, projekt_termine

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    _darf_termine(t, user)
    try:
        sched = await projekt_termine.anlegen(db, t, art=body.art, uhrzeit=body.uhrzeit, wochentag=body.wochentag,
                                              datum=body.datum, thema=body.thema,
                                              von_user_id=await _besitzer(user, db))
    except projekt_termine.TerminFehler as e:
        raise HTTPException(status_code=422, detail=str(e))
    typ, autor_id, name = await _autor(user, db)
    termin = projekt_termine.termin_dict(sched)
    await projekt_gespraech.posten(db, t, autor_typ="system", autor_id=None, autor_name="Termine",
                                   text=f"{name} hat einen Termin angelegt: "
                                        f"{projekt_termine.beschreibung(termin, body.datum)} — {termin['thema']}")
    await db.commit()
    return termin


@router.delete("/{team_id}/termine/{schedule_id}")
async def termin_entfernen(team_id: str, schedule_id: str, user=Depends(require_auth_or_agent),
                           db: AsyncSession = Depends(get_db)):
    from app.core import projekt_termine

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    _darf_termine(t, user)
    try:
        await projekt_termine.entfernen(db, t.id, schedule_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return {"status": "entfernt", "id": schedule_id}


# ─── Projektwissen: anhängen, Verlauf, aufbauen ────────────────


class WissenAbschnitt(BaseModel):
    pfad: str = Field(min_length=1, max_length=300)
    abschnitt: str = Field(min_length=1, max_length=50_000)


@router.post("/{team_id}/wissen/ergaenzen")
async def wissen_ergaenzen(team_id: str, body: WissenAbschnitt, user=Depends(require_auth_or_agent),
                           db: AsyncSession = Depends(get_db)):
    """Abschnitt an eine Seite hängen (legt sie an) — überschreibt nichts."""
    from app.core import projekt_wissen

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    _, _, name = await _autor(user, db)
    try:
        _keine_dateiseite(body.pfad)
        return await projekt_wissen.anhaengen(db, brain, body.pfad.strip(), body.abschnitt, wer=name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{team_id}/wissen/verlauf")
async def wissen_verlauf(team_id: str, pfad: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    from app.core import projekt_wissen

    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    try:
        _keine_dateiseite(pfad, lesen=True)
        return {"pfad": pfad, "versionen": projekt_wissen.verlauf(brain, pfad)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{team_id}/wissen/version")
async def wissen_version(team_id: str, pfad: str, version: str, user=Depends(require_auth_or_agent),
                         db: AsyncSession = Depends(get_db)):
    from app.core import projekt_wissen

    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    try:
        _keine_dateiseite(pfad, lesen=True)
        return {"pfad": pfad, "version": version, "inhalt": projekt_wissen.version_lesen(brain, pfad, version)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Version nicht gefunden.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class Wiederherstellen(BaseModel):
    pfad: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=7, max_length=40)


@router.post("/{team_id}/wissen/wiederherstellen")
async def wissen_wiederherstellen(team_id: str, body: Wiederherstellen, user=Depends(require_auth),
                                  db: AsyncSession = Depends(get_db)):
    """Eine frühere Version zurückholen — als neue Version, nichts geht verloren."""
    from app.core import projekt_wissen

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    brain = await _projekt_brain(t, user, db)
    try:
        _keine_dateiseite(body.pfad)
        alt = projekt_wissen.version_lesen(brain, body.pfad, body.version)
        _, _, name = await _autor(user, db)
        return await projekt_wissen.schreiben(db, brain, body.pfad, alt, wer=f"{name} (zurückgeholt)")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Version nicht gefunden.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{team_id}/wissen/aufbauen", status_code=201)
async def wissen_aufbauen(team_id: str, request: Request, router_: TaskRouter = Depends(_get_task_router),
                          user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Karte für den Lead: das Projektwissen aus Repo und Beschreibung aufbauen."""
    from app.core import projekt_wissen, projekte

    _kein_betrachter(user)
    t = await _projekt(team_id, user, db)
    quelle = f"das Repo {t.repo} (README, Dokumentation, Struktur, offene Issues)" if t.repo else "die Projektbeschreibung und vorhandene Dateien"
    text = (
        f"Lies {quelle} und baue daraus das Projektwissen auf:\n"
        f"{projekt_wissen.auftrag_index_zeile()}\n"
        "- `architektur.md`: Aufbau, Bausteine, wie man etwas startet, testet und ausliefert.\n"
        "- `glossar.md`: Fachbegriffe des Projekts.\n"
        "Schreibe mit `projekt_wissen_schreiben`; nichts erfinden, Unklares als offene Frage notieren. "
        "Keine Passwörter oder Zugangsschlüssel."
    )
    karte = projekte.neue_karte(t.id, "Projektwissen aufbauen", text, spalte="bereit", prioritaet=1, von=_wer(user),
                                ersteller_user_id=await _besitzer(user, db))
    db.add(karte)
    await db.commit()
    return await _vergeben(t, karte.id, None, user, request, router_, db)
