"""Skill Marketplace API — CRUD, assign/unassign, import, propose, rate, file attachments."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
import io
import re

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.skill import Skill, SkillStatus, SkillCategory, AgentSkillAssignment, SkillFile, SkillTaskUsage
from app.models.audit_log import AuditLog, AuditEventType
from app.core.skill_file_storage import validate_filename, save_file, read_file, delete_file, get_all_files_for_agent

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
    helpfulness: int | None = None   # 1-5: how much did the skill help?
    rating: int | None = None        # 1-5: agent self-rating of task quality
    comment: str | None = None


@router.post("/agent/record-usage", status_code=201)
async def agent_record_skill_usage(
    body: AgentRecordUsageBody,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent records explicit skill usage — called from MCP skill_rate / skill_record_usage tools.

    Creates a SkillTaskUsage row. If a rating is provided, also updates the skill's rolling avg_rating.
    """
    agent_id = auth["agent_id"]

    skill = (await db.execute(select(Skill).where(Skill.id == body.skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Resolve task context for duration/cost if task_id provided
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

    usage = SkillTaskUsage(
        skill_id=body.skill_id,
        task_id=body.task_id or "manual",
        agent_id=agent_id,
        skill_helpfulness=body.helpfulness,
        agent_self_rating=body.rating,
        task_duration_ms=task_duration_ms,
        task_cost_usd=task_cost_usd,
        time_saved_seconds=time_saved_seconds,
    )
    db.add(usage)

    # Update skill rolling avg if rating provided
    if body.rating and 1 <= body.rating <= 5:
        total = skill.usage_count or 0
        if skill.avg_rating is None:
            skill.avg_rating = float(body.rating)
        else:
            skill.avg_rating = round((skill.avg_rating * total + body.rating) / (total + 1), 2)
        skill.usage_count = total + 1

    await db.commit()
    return {
        "status": "recorded",
        "skill_id": body.skill_id,
        "avg_rating": skill.avg_rating,
        "usage_count": skill.usage_count,
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
