"""Teams API — persistent agent teams with a lead, plus task delegation."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tasks import _get_task_router
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.agent import Agent
from app.models.team import Team

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])


class CreateTeam(BaseModel):
    name: str
    description: str = ""
    member_agent_ids: list[str] = []
    lead_agent_id: str | None = None


class UpdateTeam(BaseModel):
    name: str | None = None
    description: str | None = None
    member_agent_ids: list[str] | None = None
    lead_agent_id: str | None = None


def _serialize(t: Team) -> dict:
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "member_agent_ids": t.member_agent_ids or [],
        "lead_agent_id": t.lead_agent_id, "is_active": t.is_active,
        "created_by": t.created_by,
    }


def _validate_lead(member_ids: list[str], lead_id: str | None) -> None:
    if lead_id is not None and lead_id not in (member_ids or []):
        raise HTTPException(status_code=400, detail="lead_agent_id must be one of member_agent_ids")


async def _get_team(team_id: str, db: AsyncSession) -> Team:
    t = (await db.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none()
    if not t or not t.is_active:
        raise HTTPException(status_code=404, detail="Team not found")
    return t


@router.post("/", status_code=201)
async def create_team(body: CreateTeam, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _validate_lead(body.member_agent_ids, body.lead_agent_id)
    team = Team(
        id=uuid.uuid4().hex[:32], name=body.name, description=body.description,
        member_agent_ids=body.member_agent_ids, lead_agent_id=body.lead_agent_id,
        created_by=getattr(user, "email", None) or str(getattr(user, "id", "")),
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return _serialize(team)


@router.get("/")
async def list_teams(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Team).where(Team.is_active == True))).scalars().all()  # noqa: E712
    return {"teams": [_serialize(t) for t in rows]}


@router.get("/{team_id}")
async def get_team(team_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    return _serialize(await _get_team(team_id, db))


@router.patch("/{team_id}")
async def update_team(team_id: str, body: UpdateTeam, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    if body.name is not None: t.name = body.name
    if body.description is not None: t.description = body.description
    if body.member_agent_ids is not None: t.member_agent_ids = body.member_agent_ids
    if body.lead_agent_id is not None: t.lead_agent_id = body.lead_agent_id
    _validate_lead(t.member_agent_ids, t.lead_agent_id)
    await db.commit(); await db.refresh(t)
    return _serialize(t)


@router.delete("/{team_id}")
async def delete_team(team_id: str, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    t.is_active = False
    await db.commit()
    return {"status": "deleted", "id": team_id}


class MembersChange(BaseModel):
    add: list[str] = []
    remove: list[str] = []


class SetLead(BaseModel):
    lead_agent_id: str | None


@router.post("/{team_id}/members")
async def change_members(team_id: str, body: MembersChange, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    members = list(t.member_agent_ids or [])
    for a in body.add:
        if a not in members:
            members.append(a)
    members = [m for m in members if m not in body.remove]
    t.member_agent_ids = members
    if t.lead_agent_id and t.lead_agent_id not in members:
        t.lead_agent_id = None  # lead removed -> clear
    await db.commit()
    await db.refresh(t)
    return _serialize(t)


@router.patch("/{team_id}/lead")
async def set_lead(team_id: str, body: SetLead, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    _validate_lead(t.member_agent_ids, body.lead_agent_id)
    t.lead_agent_id = body.lead_agent_id
    await db.commit()
    await db.refresh(t)
    return _serialize(t)


# ─── Task 4: delegate a task to a team via its lead ──────────────


async def _role_summary(agent: Agent, request: Request) -> str:
    """Best-effort first heading of the member's knowledge.md.

    Mirrors agents.py: reads /workspace/knowledge.md via a container exec.
    Returns the first non-empty line (leading '#' / 'Rolle:' stripped) or ""
    on ANY failure (no docker, stopped container, exec error). A missing role
    must never block delegation — keep this standalone + patchable in tests.
    """
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


@router.post("/{team_id}/tasks", status_code=201)
async def delegate_to_team(team_id: str, body: DelegateTask, request: Request,
                           router_: TaskRouter = Depends(_get_task_router),
                           user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    t = await _get_team(team_id, db)
    if not t.lead_agent_id:
        raise HTTPException(status_code=400, detail="assign a lead first")
    if not (t.member_agent_ids or []):
        raise HTTPException(status_code=400, detail="team has no members")
    roster = await _team_roster(t, db, request)
    prefix = LEAD_INSTRUCTION.format(roster=roster)
    task = await router_.create_and_route_task(
        title=body.title, prompt=prefix + "\n## Aufgabe\n" + body.prompt,
        priority=body.priority, agent_id=t.lead_agent_id,
        metadata={"team_id": t.id, "delegated_to_team": True},
    )
    return {"task_id": task.id, "lead_agent_id": t.lead_agent_id, "status": task.status}
