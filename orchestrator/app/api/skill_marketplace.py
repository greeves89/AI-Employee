"""Skill Marketplace API — CRUD, assign/unassign, import, propose, rate, file attachments."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, delete, cast, Text, update
from sqlalchemy.ext.asyncio import AsyncSession
import io
import logging
import re
from datetime import datetime, timezone

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.skill import Skill, SkillStatus, SkillCategory, AgentSkillAssignment, SkillFile, SkillTaskUsage, SkillVersion
from app.models.task import Task
from app.models.audit_log import AuditLog, AuditEventType
from app.core.skill_file_storage import validate_filename, save_file, read_file, delete_file, get_all_files_for_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills-marketplace"])


def _normalize_category(raw: str) -> SkillCategory:
    """Accept any case variant and map to valid SkillCategory, default ROUTINE."""
    try:
        return SkillCategory(raw.upper())
    except (ValueError, AttributeError):
        return SkillCategory.ROUTINE


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
        "avg_agent_duration_ms": skill.avg_agent_duration_ms,
        "manual_duration_seconds": skill.manual_duration_seconds,
        "is_public": skill.is_public,
        "current_version": skill.current_version,
        "improvement_status": skill.improvement_status,
        "improvement_proposal": skill.improvement_proposal,
        "improvement_proposed_at": skill.improvement_proposed_at.isoformat() if skill.improvement_proposed_at else None,
        "improvement_review_reason": skill.improvement_review_reason,
        "assigned_agents": assigned_agents or [],
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


async def _generate_skill_embedding(db: AsyncSession, skill: Skill) -> None:
    """Generate and store a pgvector embedding for a skill (non-blocking on failure)."""
    import logging
    from sqlalchemy import text as sa_text
    _logger = logging.getLogger(__name__)
    try:
        from app.services.embedding_service import get_embedding_service
        from app.services.embedding_client import skill_embedding_text

        svc = get_embedding_service()
        text = skill_embedding_text(skill.name, skill.description, skill.content)
        emb = await svc.embed(text)
        if emb is not None:
            await db.execute(
                sa_text("UPDATE skills SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                {"emb": str(emb), "id": skill.id},
            )
            await db.commit()
    except Exception as e:
        _logger.warning(f"Failed to generate skill embedding for '{skill.name}': {e}")


async def _snapshot_version(db: AsyncSession, skill: Skill, created_by: str, change_reason: str | None = None) -> SkillVersion:
    """Snapshot the current skill content as a new SkillVersion before mutation."""
    # Compute avg helpfulness from recent usages
    avg_h_result = await db.execute(
        select(func.avg(SkillTaskUsage.skill_helpfulness))
        .where(SkillTaskUsage.skill_id == skill.id)
        .where(SkillTaskUsage.skill_helpfulness.isnot(None))
    )
    avg_h = avg_h_result.scalar()

    version = SkillVersion(
        skill_id=skill.id,
        version_number=skill.current_version or 1,
        content=skill.content,
        description=skill.description,
        avg_helpfulness_at_snapshot=round(avg_h, 2) if avg_h else None,
        usage_count_at_snapshot=skill.usage_count or 0,
        created_by=created_by,
        change_reason=change_reason,
    )
    db.add(version)
    skill.current_version = (skill.current_version or 1) + 1
    return version


def _version_to_response(v: SkillVersion) -> dict:
    return {
        "id": v.id,
        "skill_id": v.skill_id,
        "version_number": v.version_number,
        "content": v.content,
        "description": v.description,
        "avg_helpfulness_at_snapshot": v.avg_helpfulness_at_snapshot,
        "usage_count_at_snapshot": v.usage_count_at_snapshot,
        "created_by": v.created_by,
        "change_reason": v.change_reason,
        "created_at": v.created_at.isoformat() if v.created_at else None,
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
    # Exact match
    existing = (await db.execute(select(Skill).where(Skill.name == body.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Skill '{body.name}' already exists")
    # Fuzzy: strip date suffixes (-YYYY-MM-DD or -YYYY-MM) and check base name
    base_name = re.sub(r"[-_]\d{4}[-_]\d{2}([-_]\d{2})?$", "", body.name).strip()
    if base_name != body.name:
        similar = (await db.execute(
            select(Skill).where(Skill.name.ilike(f"{base_name}%"))
        )).scalars().first()
        if similar:
            raise HTTPException(
                status_code=409,
                detail=f"Similar skill already exists: '{similar.name}'. Update it instead of creating a new version."
            )

    skill = Skill(
        name=body.name,
        description=body.description,
        content=body.content,
        category=_normalize_category(body.category),
        status=SkillStatus.ACTIVE,
        created_by="user",
        paths=body.paths,
        roles=body.roles,
        is_public=body.is_public,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)

    await _generate_skill_embedding(db, skill)

    return _to_response(skill)


@router.put("/marketplace/{skill_id}")
async def update_skill(
    skill_id: int,
    body: SkillUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a skill. Snapshots the previous version before applying changes."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    updates = body.model_dump(exclude_unset=True)
    if "content" in updates and updates["content"] != skill.content:
        await _snapshot_version(db, skill, created_by="user", change_reason="Manual update via UI")

    for field, value in updates.items():
        setattr(skill, field, value)

    await db.commit()
    await db.refresh(skill)

    if any(f in updates for f in ("name", "description", "content")):
        await _generate_skill_embedding(db, skill)

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


# --- Trend skill review ---

@router.post("/marketplace/{skill_id}/approve")
async def approve_skill(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Approve a DRAFT skill — makes it active in the marketplace."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skill.status = SkillStatus.ACTIVE
    skill.is_public = True
    await db.commit()
    return {"id": skill_id, "status": "active"}


@router.post("/marketplace/{skill_id}/reject")
async def reject_skill(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Reject a DRAFT skill — archives it."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skill.status = SkillStatus.ARCHIVED
    await db.commit()
    return {"id": skill_id, "status": "archived"}


# --- Skill improvement proposal review ---

@router.get("/marketplace/improvements/pending")
async def list_pending_improvements(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all skills with an improvement proposal awaiting review."""
    skills = (await db.execute(
        select(Skill)
        .where(Skill.improvement_status == "pending_review")
        .order_by(Skill.improvement_proposed_at.desc())
    )).scalars().all()
    return {"skills": [_to_response(s) for s in skills]}


@router.post("/marketplace/{skill_id}/approve-improvement")
async def approve_improvement(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending improvement proposal — applies it and starts A/B probation."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.improvement_status != "pending_review" or not skill.improvement_proposal:
        raise HTTPException(status_code=400, detail="No pending improvement to approve")

    proposal = skill.improvement_proposal
    # Snapshot the current (pre-improvement) content so a rollback is possible.
    await _snapshot_version(
        db, skill, created_by=f"user:{getattr(user, 'id', 'user')}",
        change_reason="Pre-improvement snapshot (proposal approved)",
    )

    skill.content = proposal.get("suggested_content", skill.content)
    if proposal.get("suggested_description"):
        skill.description = proposal["suggested_description"]
    skill.current_version = (skill.current_version or 1) + 1

    # Enter A/B validation with pre-improvement metrics from the proposal.
    skill.improvement_status = "probation"
    skill.probation_started_at = datetime.now(timezone.utc)
    skill.pre_improvement_avg_helpfulness = proposal.get("avg_helpfulness_before")
    skill.pre_improvement_rated_count = proposal.get("rated_count_before")
    skill.improvement_proposal = None
    skill.improvement_proposed_at = None

    await db.commit()
    await db.refresh(skill)
    await _generate_skill_embedding(db, skill)
    return {"id": skill_id, "improvement_status": "probation"}


@router.post("/marketplace/{skill_id}/reject-improvement")
async def reject_improvement(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending improvement proposal — discards it, skill content unchanged."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.improvement_status != "pending_review":
        raise HTTPException(status_code=400, detail="No pending improvement to reject")
    skill.improvement_status = None
    skill.improvement_proposal = None
    skill.improvement_proposed_at = None
    skill.improvement_review_reason = None
    await db.commit()
    return {"id": skill_id, "improvement_status": None}


# --- Version history & rollback ---

@router.get("/marketplace/{skill_id}/versions")
async def list_skill_versions(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all historical versions of a skill, newest first."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    result = await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.version_number.desc())
    )
    versions = list(result.scalars().all())
    return {
        "skill_id": skill_id,
        "current_version": skill.current_version,
        "versions": [_version_to_response(v) for v in versions],
        "total": len(versions),
    }


@router.get("/marketplace/{skill_id}/versions/{version_id}")
async def get_skill_version(
    skill_id: int,
    version_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific version's full content."""
    version = (await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id, SkillVersion.id == version_id)
    )).scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_to_response(version)


@router.post("/marketplace/{skill_id}/rollback/{version_id}")
async def rollback_skill(
    skill_id: int,
    version_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Rollback a skill to a previous version. Snapshots current content first."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    target = (await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id, SkillVersion.id == version_id)
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target version not found")

    await _snapshot_version(
        db, skill,
        created_by="rollback",
        change_reason=f"Rolled back to version {target.version_number}",
    )

    skill.content = target.content
    skill.description = target.description
    await db.commit()
    await db.refresh(skill)

    return {
        "status": "rolled_back",
        "skill_id": skill_id,
        "rolled_back_to_version": target.version_number,
        "current_version": skill.current_version,
    }


# --- Assignment ---

@router.post("/marketplace/{skill_id}/assign")
async def assign_skill(
    skill_id: int,
    body: SkillAssign,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Assign a skill to an agent and push any file attachments into the agent workspace."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    existing = (await db.execute(
        select(AgentSkillAssignment)
        .where(AgentSkillAssignment.agent_id == body.agent_id)
        .where(AgentSkillAssignment.skill_id == skill_id)
    )).scalar_one_or_none()
    if existing:
        # Still push files in case they changed since last assignment
        await _push_skill_files_to_agent(request, db, skill_id, skill.name, body.agent_id)
        return {"status": "already_assigned"}

    assignment = AgentSkillAssignment(
        agent_id=body.agent_id,
        skill_id=skill_id,
        assigned_by="user",
    )
    db.add(assignment)
    await db.commit()

    await _push_skill_files_to_agent(request, db, skill_id, skill.name, body.agent_id)
    return {"status": "assigned", "agent_id": body.agent_id, "skill_id": skill_id}


async def _push_skill_files_to_agent(request, db: AsyncSession, skill_id: int, skill_name: str, agent_id: str) -> None:
    """Push all skill file attachments into the agent's container workspace."""
    files = get_all_files_for_agent(skill_id)
    if not files:
        return
    try:
        from app.models.agent import Agent
        from app.dependencies import get_docker_service
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
        if not agent or not agent.container_id:
            return
        docker = get_docker_service(request)
        target_dir = f"/workspace/skills/{skill_name}"
        docker.write_files_in_container(agent.container_id, target_dir, files)
        import logging
        logging.getLogger(__name__).info(
            f"Pushed {len(files)} file(s) for skill '{skill_name}' to agent {agent_id}"
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not push skill files to agent {agent_id}: {e}")


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


@router.post("/agent/install/{skill_id}", status_code=201)
async def agent_install_skill(
    skill_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent self-assigns a skill from the marketplace. Returns skill content for immediate use."""
    agent_id = auth["agent_id"]

    skill = (await db.execute(
        select(Skill).where(Skill.id == skill_id, Skill.status == SkillStatus.ACTIVE)
    )).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found or not active")

    existing = (await db.execute(
        select(AgentSkillAssignment).where(
            AgentSkillAssignment.skill_id == skill_id,
            AgentSkillAssignment.agent_id == agent_id,
        )
    )).scalar_one_or_none()

    if not existing:
        db.add(AgentSkillAssignment(
            skill_id=skill_id,
            agent_id=agent_id,
            assigned_by=f"agent:{agent_id}",
        ))
        await db.commit()
        await _push_skill_files_to_agent(request, db, skill_id, skill.name, agent_id)

    # Track install as a usage event so analytics reflects actual use in the current task
    from app.models.task import Task, TaskStatus as TS
    running_task = (await db.execute(
        select(Task.id).where(
            Task.agent_id == agent_id,
            Task.status == TS.RUNNING,
        ).order_by(Task.created_at.desc()).limit(1)
    )).scalar_one_or_none()

    if running_task:
        usage_exists = (await db.execute(
            select(SkillTaskUsage).where(
                SkillTaskUsage.skill_id == skill_id,
                SkillTaskUsage.task_id == running_task,
                SkillTaskUsage.agent_id == agent_id,
            )
        )).scalar_one_or_none()
        if not usage_exists:
            db.add(SkillTaskUsage(
                skill_id=skill_id, task_id=running_task, agent_id=agent_id,
                skill_version=skill.current_version,
            ))
            skill.usage_count = (skill.usage_count or 0) + 1
            await db.commit()

    return {
        "status": "installed" if not existing else "already_installed",
        "skill_id": skill_id,
        "skill_name": skill.name,
        "content": skill.content,
    }


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


class AgentRecordUsageBody(BaseModel):
    skill_id: int
    task_id: str | None = None
    chat_session_id: str | None = None  # set instead of task_id when used in a chat
    helpfulness: int | None = None    # 1-5: how much did the skill help?
    rating: int | None = None         # 1-5: agent self-rating of task quality
    user_rating: int | None = None    # 1-5: user feedback interpreted by agent from chat
    comment: str | None = None


@router.post("/agent/record-usage", status_code=201)
async def agent_record_skill_usage(
    body: AgentRecordUsageBody,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent records explicit skill usage — called from MCP skill_rate tool.

    Works both for tasks (keyed on task_id) and for chat sessions (no task).
    Upserts a SkillTaskUsage row so a follow-up call carrying the user's
    feedback rating updates the same record. Updates skill rolling averages.
    """
    agent_id = auth["agent_id"]

    skill = (await db.execute(select(Skill).where(Skill.id == body.skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    is_chat = not body.task_id
    source = "chat" if is_chat else "task"

    # Resolve task timing — may be None if task is still running (backfilled later)
    task_duration_ms = None
    task_cost_usd = None
    time_saved_seconds = None
    if body.task_id:
        from app.models.task import Task
        task = (await db.execute(select(Task).where(Task.id == body.task_id))).scalar_one_or_none()
        if task:
            task_duration_ms = task.duration_ms
            task_cost_usd = task.cost_usd
            if skill.manual_duration_seconds and task.duration_ms:
                agent_secs = task.duration_ms / 1000
                time_saved_seconds = max(0, int(skill.manual_duration_seconds - agent_secs))

    # Upsert. For tasks: key on task_id+skill. For chat: there is no stable
    # key, so update the most recent chat usage of this skill by this agent
    # within the last 24h (the "use → wait for feedback → rate" follow-up).
    if is_chat:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        existing = (await db.execute(
            select(SkillTaskUsage)
            .where(
                SkillTaskUsage.task_id.is_(None),
                SkillTaskUsage.skill_id == body.skill_id,
                SkillTaskUsage.agent_id == agent_id,
                SkillTaskUsage.created_at >= cutoff,
            )
            .order_by(SkillTaskUsage.created_at.desc())
        )).scalars().first()
    else:
        existing = (await db.execute(
            select(SkillTaskUsage).where(
                SkillTaskUsage.task_id == body.task_id,
                SkillTaskUsage.skill_id == body.skill_id,
                SkillTaskUsage.agent_id == agent_id,
            )
        )).scalar_one_or_none()

    is_new = existing is None
    if existing:
        if body.helpfulness is not None:
            existing.skill_helpfulness = body.helpfulness
        if body.rating is not None:
            existing.agent_self_rating = body.rating
        if body.user_rating is not None:
            existing.user_rating = body.user_rating
        if task_duration_ms is not None:
            existing.task_duration_ms = task_duration_ms
        if task_cost_usd is not None:
            existing.task_cost_usd = task_cost_usd
        if time_saved_seconds is not None:
            existing.time_saved_seconds = time_saved_seconds
        usage = existing
    else:
        usage = SkillTaskUsage(
            skill_id=body.skill_id,
            task_id=body.task_id,
            chat_session_id=body.chat_session_id,
            source=source,
            agent_id=agent_id,
            skill_helpfulness=body.helpfulness,
            agent_self_rating=body.rating,
            user_rating=body.user_rating,
            skill_version=skill.current_version,
            task_duration_ms=task_duration_ms,
            task_cost_usd=task_cost_usd,
            time_saved_seconds=time_saved_seconds,
        )
        db.add(usage)

    # Update skill rolling averages (only on new records to avoid double-counting)
    if is_new:
        skill.usage_count = (skill.usage_count or 0) + 1
        if body.rating and 1 <= body.rating <= 5:
            total = skill.usage_count
            if skill.avg_rating is None:
                skill.avg_rating = float(body.rating)
            else:
                skill.avg_rating = round((skill.avg_rating * (total - 1) + body.rating) / total, 2)
    elif body.rating and 1 <= body.rating <= 5 and existing and existing.agent_self_rating != body.rating:
        # Rating changed on existing record — recalculate is complex, do simple EMA update
        skill.avg_rating = round(
            (skill.avg_rating or body.rating) * 0.9 + body.rating * 0.1, 2
        ) if skill.avg_rating else float(body.rating)

    await db.commit()
    return {
        "status": "updated" if not is_new else "recorded",
        "skill_id": body.skill_id,
        "avg_rating": skill.avg_rating,
        "usage_count": skill.usage_count,
    }


@router.get("/agent/search")
async def agent_search_skills(
    q: str = Query(""),
    category: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    task_id: str | None = Query(None),
    semantic: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent searches the skill marketplace with hybrid semantic + keyword search.

    When semantic=True (default) and the embedding service is available, uses
    pgvector cosine similarity to find semantically related skills — even when
    keywords don't match. Falls back to keyword-only if embeddings are unavailable.

    If task_id is provided and skills are found, records an implicit usage entry
    for the top result.
    """
    import logging
    from sqlalchemy import or_, text as sa_text

    _logger = logging.getLogger(__name__)
    skills = []
    search_mode = "keyword"

    # Semantic search: use pgvector cosine similarity (like brain_search)
    if q and semantic:
        try:
            from app.services.embedding_service import get_embedding_service

            svc = get_embedding_service()
            if svc.enabled:
                qvec = await svc.embed(q)
                if qvec is not None:
                    cat_filter = "AND UPPER(category) = :cat" if category else ""
                    rows = (await db.execute(
                        sa_text(
                            f"""
                            SELECT id,
                                   1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
                            FROM skills
                            WHERE status = 'ACTIVE'
                              AND embedding IS NOT NULL
                              {cat_filter}
                            ORDER BY embedding <=> CAST(:qvec AS vector)
                            LIMIT :limit
                            """
                        ),
                        {"qvec": str(qvec), "limit": limit, "cat": category.upper() if category else None},
                    )).mappings().all()

                    if rows:
                        skill_ids = [r["id"] for r in rows]
                        result = await db.execute(
                            select(Skill).where(Skill.id.in_(skill_ids))
                        )
                        skill_map = {s.id: s for s in result.scalars().all()}
                        skills = [skill_map[sid] for sid in skill_ids if sid in skill_map]
                        search_mode = "semantic"
        except Exception as e:
            _logger.warning(f"Semantic skill search failed, falling back to keyword: {e}")

    # Keyword fallback
    if not skills:
        query = select(Skill).where(Skill.status == SkillStatus.ACTIVE)
        if q:
            words = [w for w in q.split() if len(w) >= 3][:6]
            if words:
                conditions = [
                    Skill.name.ilike(f"%{w}%") | Skill.description.ilike(f"%{w}%")
                    for w in words
                ]
                query = query.where(or_(*conditions))
        if category:
            query = query.where(cast(Skill.category, Text) == category.upper())
        query = query.order_by(Skill.usage_count.desc()).limit(limit)
        skills = list((await db.execute(query)).scalars().all())

    # Build the response BEFORE any commit below. The usage-tracking block
    # commits/rolls back, which expires the ORM objects; accessing their
    # columns afterwards would trigger an async lazy-load -> MissingGreenlet
    # -> 500. Serialising here (objects still fresh) makes the search robust.
    skill_responses = [_to_response(s) for s in skills]

    # Implicit usage tracking
    if skills:
        agent_id = auth["agent_id"]
        resolved_task_id = task_id
        if not resolved_task_id:
            from app.models.task import Task, TaskStatus
            running = (await db.execute(
                select(Task.id).where(
                    Task.agent_id == agent_id,
                    Task.status == TaskStatus.RUNNING,
                ).order_by(Task.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            resolved_task_id = running

        if resolved_task_id and q.strip():
            top_skill = skills[0]
            existing = (await db.execute(
                select(SkillTaskUsage).where(
                    SkillTaskUsage.task_id == resolved_task_id,
                    SkillTaskUsage.skill_id == top_skill.id,
                    SkillTaskUsage.agent_id == agent_id,
                )
            )).scalar_one_or_none()
            if not existing:
                # Usage tracking is a best-effort side effect — it must NEVER turn
                # the skill search into a 500. Two fixes vs. the old code:
                #  1) resolved_task_id can be a synthetic chat id (not a real Task),
                #     which violated the tasks FK. Only put it in task_id when it is
                #     a real task; otherwise record it as a chat usage.
                #  2) Increment the counter with an atomic UPDATE — never read an
                #     expired ORM attribute after a rollback (that caused the
                #     MissingGreenlet -> 500). On any failure: rollback + log, and
                #     still return the search result.
                try:
                    is_real_task = bool(await db.scalar(
                        select(Task.id).where(Task.id == resolved_task_id)
                    ))
                    db.add(SkillTaskUsage(
                        skill_id=top_skill.id,
                        task_id=resolved_task_id if is_real_task else None,
                        chat_session_id=None if is_real_task else resolved_task_id,
                        source="task" if is_real_task else "chat",
                        agent_id=agent_id,
                        skill_version=top_skill.current_version,
                    ))
                    await db.execute(
                        update(Skill)
                        .where(Skill.id == top_skill.id)
                        .values(usage_count=func.coalesce(Skill.usage_count, 0) + 1)
                    )
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.warning("skill usage tracking failed (non-fatal): %s", e)

    return {"skills": skill_responses, "total": len(skills), "mode": search_mode}


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


@router.get("/agent/files/{skill_id}/{filename}")
async def agent_download_skill_file(
    skill_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent downloads a file attachment for a skill it has assigned."""
    agent_id = auth["agent_id"]

    # Verify the agent has this skill assigned
    assignment = (await db.execute(
        select(AgentSkillAssignment)
        .where(AgentSkillAssignment.agent_id == agent_id)
        .where(AgentSkillAssignment.skill_id == skill_id)
    )).scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=403, detail="Skill not assigned to this agent")

    skill_file = (await db.execute(
        select(SkillFile).where(SkillFile.skill_id == skill_id, SkillFile.filename == filename)
    )).scalar_one_or_none()
    if not skill_file:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        data = read_file(skill_id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found on disk")

    audit = AuditLog(
        agent_id=agent_id,
        event_type=AuditEventType.SKILL_FILE_DOWNLOADED.value,
        command=filename,
        outcome="success",
        meta={"skill_id": skill_id, "source": "agent"},
    )
    db.add(audit)
    await db.commit()

    return StreamingResponse(
        io.BytesIO(data),
        media_type=skill_file.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    if skill.avg_rating is None:
        skill.avg_rating = body.rating
    else:
        total = skill.usage_count or 1
        skill.avg_rating = round((skill.avg_rating * total + body.rating) / (total + 1), 2)

    skill.usage_count += 1
    await db.commit()
    await db.refresh(skill)
    return _to_response(skill)


class ManualDurationBody(BaseModel):
    manual_duration_seconds: int | None = None


@router.patch("/marketplace/{skill_id}/manual-duration")
async def set_skill_manual_duration(
    skill_id: int,
    body: ManualDurationBody,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Set the estimated manual effort for a skill — used for ROI / time-savings analytics."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill.manual_duration_seconds = body.manual_duration_seconds
    await db.commit()
    await db.refresh(skill)
    return {"id": skill.id, "manual_duration_seconds": skill.manual_duration_seconds}


# --- File Attachments ---

@router.post("/marketplace/{skill_id}/files", status_code=201)
async def upload_skill_file(
    skill_id: int,
    file: UploadFile = File(...),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment to a skill."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        safe_name = validate_filename(file.filename or "upload.bin")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check for duplicate filename
    existing = (await db.execute(
        select(SkillFile).where(SkillFile.skill_id == skill_id, SkillFile.filename == safe_name)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"File '{safe_name}' already exists for this skill. Delete it first.")

    data = await file.read()
    try:
        path = save_file(skill_id, safe_name, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    skill_file = SkillFile(
        skill_id=skill_id,
        filename=safe_name,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_path=str(path),
    )
    db.add(skill_file)

    audit = AuditLog(
        agent_id="user",
        event_type=AuditEventType.SKILL_FILE_UPLOADED.value,
        command=safe_name,
        outcome="success",
        user_id=str(getattr(user, "id", "unknown")),
        meta={"skill_id": skill_id, "size_bytes": len(data)},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(skill_file)
    return _file_to_response(skill_file)


@router.get("/marketplace/{skill_id}/files")
async def list_skill_files(
    skill_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all file attachments for a skill."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    result = await db.execute(
        select(SkillFile).where(SkillFile.skill_id == skill_id).order_by(SkillFile.filename)
    )
    files = result.scalars().all()
    return {"files": [_file_to_response(f) for f in files]}


@router.get("/marketplace/{skill_id}/files/{filename}")
async def download_skill_file(
    skill_id: int,
    filename: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Download a skill file attachment."""
    skill_file = (await db.execute(
        select(SkillFile).where(SkillFile.skill_id == skill_id, SkillFile.filename == filename)
    )).scalar_one_or_none()
    if not skill_file:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        data = read_file(skill_id, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found on disk")

    audit = AuditLog(
        agent_id="user",
        event_type=AuditEventType.SKILL_FILE_DOWNLOADED.value,
        command=filename,
        outcome="success",
        user_id=str(getattr(user, "id", "unknown")),
        meta={"skill_id": skill_id},
    )
    db.add(audit)
    await db.commit()

    return StreamingResponse(
        io.BytesIO(data),
        media_type=skill_file.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/marketplace/{skill_id}/files/{filename}")
async def delete_skill_file(
    skill_id: int,
    filename: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a skill file attachment."""
    skill_file = (await db.execute(
        select(SkillFile).where(SkillFile.skill_id == skill_id, SkillFile.filename == filename)
    )).scalar_one_or_none()
    if not skill_file:
        raise HTTPException(status_code=404, detail="File not found")

    delete_file(skill_id, filename)
    await db.delete(skill_file)

    audit = AuditLog(
        agent_id="user",
        event_type=AuditEventType.SKILL_FILE_DELETED.value,
        command=filename,
        outcome="success",
        user_id=str(getattr(user, "id", "unknown")),
        meta={"skill_id": skill_id},
    )
    db.add(audit)
    await db.commit()
    return {"deleted": filename}


def _file_to_response(f: SkillFile) -> dict:
    return {
        "id": f.id,
        "skill_id": f.skill_id,
        "filename": f.filename,
        "content_type": f.content_type,
        "size_bytes": f.size_bytes,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


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

    await _generate_skill_embedding(db, skill)

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
        category=_normalize_category(body.category),
        status=SkillStatus.ACTIVE,
        created_by=f"agent:{agent_id}",
        source_task_id=body.task_id,
    )
    db.add(skill)
    await db.flush()  # get skill.id before commit

    # Auto-assign to the proposing agent so it appears in "Meine Skills"
    assignment = AgentSkillAssignment(
        agent_id=agent_id,
        skill_id=skill.id,
        assigned_by="agent",
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(skill)

    # Notify user about the proposal (use separate session to avoid polluting the skill tx)
    try:
        from app.db.session import async_session_factory
        from app.models.notification import Notification
        async with async_session_factory() as notif_db:
            notif = Notification(
                type="skill_proposed",
                title=f"Neuer Skill erstellt: {body.name}",
                message=f"Agent {agent_id} hat den Skill '{body.name}' zum Katalog hinzugefügt: {body.description}",
                priority="medium",
                agent_id=agent_id,
            )
            notif_db.add(notif)
            await notif_db.commit()
    except Exception:
        pass

    await _generate_skill_embedding(db, skill)

    return _to_response(skill)


@router.patch("/agent/{skill_id}", status_code=200)
async def agent_update_skill(
    skill_id: int,
    body: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent refines a skill it created (e.g. after user feedback)."""
    import logging
    from datetime import datetime, timedelta, timezone
    from app.models.notification import Notification
    from app.models.task import Task
    from app.models.task_rating import TaskRating

    _logger = logging.getLogger(__name__)
    agent_id = auth["agent_id"]

    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Allow update if the agent created it OR has it assigned (e.g. improvement tasks)
    is_creator = skill.created_by == f"agent:{agent_id}"
    has_assignment = (await db.execute(
        select(AgentSkillAssignment.id).where(
            AgentSkillAssignment.skill_id == skill_id,
            AgentSkillAssignment.agent_id == agent_id,
        )
    )).scalar_one_or_none() is not None
    if not is_creator and not has_assignment:
        raise HTTPException(status_code=403, detail="You can only update skills you created or have installed")

    if body.content is not None and body.content != skill.content:
        reason = body.feedback or f"Agent update by {agent_id}"
        await _snapshot_version(db, skill, created_by=f"agent:{agent_id}", change_reason=reason)

    if body.description is not None:
        skill.description = body.description
    if body.content is not None:
        changelog = f"\n\n---\n*Updated by agent:{agent_id}*"
        if body.feedback:
            changelog += f" based on feedback: {body.feedback}"
        skill.content = body.content + changelog

    if body.description is not None:
        skill.description = body.description

    await db.commit()
    await db.refresh(skill)

    if body.content is not None or body.description is not None:
        await _generate_skill_embedding(db, skill)

    # Notify users whose feedback triggered this improvement (reuse engine logic)
    if body.feedback and "Auto-improvement" in (body.feedback or ""):
        try:
            from app.services.improvement_engine import _notify_feedback_contributors
            await _notify_feedback_contributors(db, skill, avg_helpfulness=0.0, rated_count=0)
            await db.commit()
        except Exception as e:
            _logger.warning(f"Could not send skill improvement notifications: {e}")

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
