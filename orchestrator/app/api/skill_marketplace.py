"""Skill Marketplace API — CRUD, assign/unassign, import, propose, rate."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.skill import Skill, SkillStatus, SkillCategory, AgentSkillAssignment

router = APIRouter(prefix="/skills", tags=["skills-marketplace"])


# --- Schemas ---

class SkillCreate(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    category: str = "routine"
    paths: list[str] | None = None
    roles: list[str] | None = None
    is_public: bool = True


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    category: str | None = None
    status: str | None = None
    paths: list[str] | None = None
    roles: list[str] | None = None
    is_public: bool | None = None


class SkillImport(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    category: str = "tool"
    source_url: str | None = None
    source_repo: str | None = None


class SkillPropose(BaseModel):
    """Agent proposes a new skill (lands as draft)."""
    name: str
    description: str
    content: str
    category: str = "pattern"
    task_id: str | None = None  # task that produced this skill


class SkillUpdate(BaseModel):
    """Agent updates an existing skill it created."""
    description: str | None = None
    content: str | None = None
    feedback: str | None = None  # human-readable changelog for this update


class SkillAssign(BaseModel):
    agent_id: str
    skill_id: int


class SkillRate(BaseModel):
    rating: float  # 1-5


def _to_response(skill: Skill, assigned_agents: list[str] | None = None) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "content": skill.content,
        "category": skill.category.value if isinstance(skill.category, SkillCategory) else skill.category,
        "status": skill.status.value if isinstance(skill.status, SkillStatus) else skill.status,
        "created_by": skill.created_by,
        "source_url": skill.source_url,
        "source_repo": skill.source_repo,
        "paths": skill.paths,
        "roles": skill.roles,
        "usage_count": skill.usage_count,
        "avg_rating": skill.avg_rating,
        "is_public": skill.is_public,
        "assigned_agents": assigned_agents or [],
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


# --- User-facing endpoints ---

@router.get("/marketplace")
async def list_skills(
    category: str | None = Query(None),
    status: str | None = Query(None, description="Filter by status (draft/active/archived)"),
    q: str | None = Query(None),
    agent_id: str | None = Query(None, description="Show assignment status for this agent"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all skills in the marketplace."""
    query = select(Skill)
    if category:
        query = query.where(Skill.category == category)
    if status:
        query = query.where(Skill.status == status)
    else:
        # Default: show active + draft (not archived)
        query = query.where(Skill.status != SkillStatus.ARCHIVED)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            Skill.name.ilike(pattern) | Skill.description.ilike(pattern)
        )

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = query.order_by(Skill.usage_count.desc(), Skill.name).offset(offset).limit(limit)
    skills = list((await db.execute(query)).scalars().all())

    # If agent_id provided, fetch their assignments
    agent_assignments: set[int] = set()
    if agent_id:
        result = await db.execute(
            select(AgentSkillAssignment.skill_id)
            .where(AgentSkillAssignment.agent_id == agent_id)
        )
        agent_assignments = {row[0] for row in result}

    return {
        "skills": [
            {**_to_response(s), "assigned_to_agent": s.id in agent_assignments}
            for s in skills
        ],
        "total": total,
    }


@router.get("/marketplace/{skill_id}")
async def get_skill(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a single skill with its full content and assigned agents."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Get assigned agents
    result = await db.execute(
        select(AgentSkillAssignment.agent_id)
        .where(AgentSkillAssignment.skill_id == skill_id)
    )
    agents = [row[0] for row in result]
    return _to_response(skill, assigned_agents=agents)


@router.post("/marketplace", status_code=201)
async def create_skill(
    body: SkillCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new skill (user-created, immediately active)."""
    existing = (await db.execute(select(Skill).where(Skill.name == body.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Skill '{body.name}' already exists")

    skill = Skill(
        name=body.name,
        description=body.description,
        content=body.content,
        category=body.category,
        status=SkillStatus.ACTIVE,
        created_by="user",
        paths=body.paths,
        roles=body.roles,
        is_public=body.is_public,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return _to_response(skill)


@router.put("/marketplace/{skill_id}")
async def update_skill(
    skill_id: int,
    body: SkillUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a skill."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(skill, field, value)

    await db.commit()
    await db.refresh(skill)
    return _to_response(skill)


@router.delete("/marketplace/{skill_id}")
async def delete_skill(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a skill and all its assignments."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    await db.execute(delete(AgentSkillAssignment).where(AgentSkillAssignment.skill_id == skill_id))
    await db.delete(skill)
    await db.commit()
    return {"deleted": skill_id}


# --- Assignment ---

@router.post("/marketplace/{skill_id}/assign")
async def assign_skill(
    skill_id: int,
    body: SkillAssign,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Assign a skill to an agent."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    existing = (await db.execute(
        select(AgentSkillAssignment)
        .where(AgentSkillAssignment.agent_id == body.agent_id)
        .where(AgentSkillAssignment.skill_id == skill_id)
    )).scalar_one_or_none()
    if existing:
        return {"status": "already_assigned"}

    assignment = AgentSkillAssignment(
        agent_id=body.agent_id,
        skill_id=skill_id,
        assigned_by="user",
    )
    db.add(assignment)
    await db.commit()
    return {"status": "assigned", "agent_id": body.agent_id, "skill_id": skill_id}


@router.delete("/marketplace/{skill_id}/unassign/{agent_id}")
async def unassign_skill(
    skill_id: int,
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Unassign a skill from an agent."""
    await db.execute(
        delete(AgentSkillAssignment)
        .where(AgentSkillAssignment.agent_id == agent_id)
        .where(AgentSkillAssignment.skill_id == skill_id)
    )
    await db.commit()
    return {"status": "unassigned"}


@router.get("/agent/available")
async def agent_available_skills(
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Get all active skills assigned to this agent (for task-start injection)."""
    agent_id = auth["agent_id"]

    # Explicitly assigned skills
    assigned = await db.execute(
        select(Skill)
        .join(AgentSkillAssignment, AgentSkillAssignment.skill_id == Skill.id)
        .where(AgentSkillAssignment.agent_id == agent_id)
        .where(Skill.status == SkillStatus.ACTIVE)
    )
    skills = list(assigned.scalars().all())

    # Also include role-matched skills (auto-assign by role)
    # Get agent's role from config
    from app.models.agent import Agent
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent:
        agent_role = agent.config.get("role", "") if agent.config else ""
        if agent_role:
            role_skills = await db.execute(
                select(Skill)
                .where(Skill.status == SkillStatus.ACTIVE)
                .where(Skill.roles.isnot(None))
            )
            for s in role_skills.scalars().all():
                if s.roles and agent_role in s.roles and s.id not in {sk.id for sk in skills}:
                    skills.append(s)

    return {
        "skills": [{"name": s.name, "content": s.content, "description": s.description} for s in skills],
        "total": len(skills),
    }


@router.get("/agent/search")
async def agent_search_skills(
    q: str = Query(""),
    category: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent searches the skill marketplace."""
    query = select(Skill).where(Skill.status == SkillStatus.ACTIVE)
    if q:
        query = query.where(
            Skill.name.ilike(f"%{q}%") | Skill.description.ilike(f"%{q}%")
        )
    if category:
        query = query.where(Skill.category == category)
    query = query.order_by(Skill.usage_count.desc()).limit(limit)
    skills = list((await db.execute(query)).scalars().all())
    return {"skills": [_to_response(s) for s in skills], "total": len(skills)}


@router.get("/agent/{agent_id}")
async def get_agent_skills(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get all skills assigned to a specific agent."""
    result = await db.execute(
        select(Skill)
        .join(AgentSkillAssignment, AgentSkillAssignment.skill_id == Skill.id)
        .where(AgentSkillAssignment.agent_id == agent_id)
        .where(Skill.status == SkillStatus.ACTIVE)
        .order_by(Skill.name)
    )
    skills = list(result.scalars().all())
    return {"skills": [_to_response(s) for s in skills], "total": len(skills)}


# --- Rating ---

@router.post("/marketplace/{skill_id}/rate")
async def rate_skill(
    skill_id: int,
    body: SkillRate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Rate a skill (running average)."""
    if not 1 <= body.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be 1-5")

    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Running average
    if skill.avg_rating is None:
        skill.avg_rating = body.rating
    else:
        # Weighted: old average counts for usage_count, new rating counts for 1
        total = skill.usage_count or 1
        skill.avg_rating = round((skill.avg_rating * total + body.rating) / (total + 1), 2)

    skill.usage_count += 1
    await db.commit()
    await db.refresh(skill)
    return _to_response(skill)


# --- Review (approve/reject agent-proposed skills) ---

@router.post("/marketplace/{skill_id}/approve")
async def approve_skill(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Approve a draft skill (make it active)."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.status != SkillStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only draft skills can be approved")
    skill.status = SkillStatus.ACTIVE
    await db.commit()
    return _to_response(skill)


@router.post("/marketplace/{skill_id}/reject")
async def reject_skill(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Reject/archive a draft skill."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skill.status = SkillStatus.ARCHIVED
    await db.commit()
    return _to_response(skill)


# --- Import (bulk from crawler or manual) ---

@router.post("/marketplace/import", status_code=201)
async def import_skill(
    body: SkillImport,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Import a skill from an external source."""
    existing = (await db.execute(select(Skill).where(Skill.name == body.name))).scalar_one_or_none()
    if existing:
        return _to_response(existing)  # Idempotent

    skill = Skill(
        name=body.name,
        description=body.description,
        content=body.content,
        category=body.category,
        status=SkillStatus.ACTIVE,
        created_by=f"import:{body.source_repo or 'manual'}",
        source_url=body.source_url,
        source_repo=body.source_repo,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return _to_response(skill)


# --- Agent-facing endpoints (agents propose skills via MCP) ---

@router.post("/agent/propose", status_code=201)
async def agent_propose_skill(
    body: SkillPropose,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent proposes a new skill (created as draft, needs user review)."""
    agent_id = auth["agent_id"]

    existing = (await db.execute(select(Skill).where(Skill.name == body.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Skill '{body.name}' already exists")

    skill = Skill(
        name=body.name,
        description=body.description,
        content=body.content,
        category=body.category,
        status=SkillStatus.DRAFT,
        created_by=f"agent:{agent_id}",
        source_task_id=body.task_id,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)

    # Notify user about the proposal (use separate session to avoid polluting the skill tx)
    try:
        from app.db.session import async_session_factory
        from app.models.notification import Notification
        async with async_session_factory() as notif_db:
            notif = Notification(
                type="skill_proposed",
                title=f"Neuer Skill vorgeschlagen: {body.name}",
                message=f"Agent {agent_id} hat den Skill '{body.name}' vorgeschlagen: {body.description}",
                priority="medium",
                agent_id=agent_id,
            )
            notif_db.add(notif)
            await notif_db.commit()
    except Exception:
        pass

    return _to_response(skill)


@router.patch("/agent/{skill_id}", status_code=200)
async def agent_update_skill(
    skill_id: int,
    body: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent refines a skill it created (e.g. after user feedback)."""
    agent_id = auth["agent_id"]

    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.created_by != f"agent:{agent_id}":
        raise HTTPException(status_code=403, detail="You can only update skills you created")

    if body.description is not None:
        skill.description = body.description
    if body.content is not None:
        old_content = skill.content
        changelog = f"\n\n---\n*Updated by agent:{agent_id}*"
        if body.feedback:
            changelog += f" based on feedback: {body.feedback}"
        skill.content = body.content + changelog

    await db.commit()
    await db.refresh(skill)
    return _to_response(skill)


@router.post("/marketplace/seed")
async def seed_from_crawler(
    user=Depends(require_auth),
):
    """Trigger the skill crawler to import external skills into the DB marketplace."""
    try:
        from app.dependencies import get_redis_service
        from app.services.skill_crawler import SkillCrawlerService
        from app.services.redis_service import RedisService
        import redis.asyncio as aioredis
        from app.config import settings

        # Create a temporary redis service for the crawler
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        redis_svc = RedisService()
        redis_svc.client = client

        crawler = SkillCrawlerService(redis_svc)
        skills = await crawler.crawl()
        await client.aclose()
        return {"status": "ok", "imported": len(skills)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Crawl failed: {e}")
