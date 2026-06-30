"""Teams API — persistent agent teams with a lead, plus task delegation."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
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
