"""API endpoints for agent templates."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import AgentManager
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.models.agent_template import AgentTemplate
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    display_name: str
    description: str = ""
    icon: str = "Bot"
    category: str = "general"
    model: str = "claude-sonnet-4-6"
    role: str = ""
    permissions: list[str] = []
    integrations: list[str] = []
    mcp_server_ids: list[int] = []
    knowledge_template: str = ""
    claude_md: str = ""


class TemplateUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    model: str | None = None
    role: str | None = None
    permissions: list[str] | None = None
    integrations: list[str] | None = None
    mcp_server_ids: list[int] | None = None
    knowledge_template: str | None = None
    claude_md: str | None = None


class CreateFromTemplate(BaseModel):
    name: str | None = None  # Override agent name


def _template_to_dict(t: AgentTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "display_name": t.display_name,
        "description": t.description,
        "icon": t.icon,
        "category": t.category,
        "model": t.model,
        "role": t.role,
        "permissions": t.permissions or [],
        "integrations": t.integrations or [],
        "mcp_server_ids": t.mcp_server_ids or [],
        "knowledge_template": t.knowledge_template,
        "claude_md": t.claude_md or "",
        "is_builtin": t.is_builtin,
        "is_published": t.is_published,
        "published_at": t.published_at.isoformat() if t.published_at else None,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("")
async def list_templates(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    List templates.
    - Admins see ALL templates (published + unpublished drafts).
    - Regular users see only published templates.
    """
    from app.models.user import UserRole

    query = select(AgentTemplate).order_by(
        AgentTemplate.is_builtin.desc(),
        AgentTemplate.category,
        AgentTemplate.display_name,
    )

    # Non-admins only see published templates
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        query = query.where(AgentTemplate.is_published == True)  # noqa: E712

    result = await db.execute(query)
    templates = result.scalars().all()
    return {"templates": [_template_to_dict(t) for t in templates]}


@router.get("/{template_id}")
async def get_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a single template by ID. Non-admins can only see published templates."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if user.role not in (UserRole.ADMIN, UserRole.MANAGER) and not template.is_published:
        raise HTTPException(status_code=404, detail="Template not found")

    return _template_to_dict(template)


@router.post("", status_code=201)
async def create_template(
    body: TemplateCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a custom template (admin/manager only — drafts are not visible to users yet)."""
    from app.models.user import UserRole
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Only admins and managers can create templates")

    existing = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.name == body.name)
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Template '{body.name}' already exists")

    template = AgentTemplate(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        icon=body.icon,
        category=body.category,
        model=body.model,
        role=body.role,
        permissions=body.permissions,
        integrations=body.integrations,
        mcp_server_ids=body.mcp_server_ids,
        knowledge_template=body.knowledge_template,
        claude_md=body.claude_md,
        is_builtin=False,
        is_published=False,
        created_by=user.id if user.id != "__anonymous__" else None,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_to_dict(template)


@router.patch("/{template_id}")
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a template (builtin templates: admin only; custom templates: owner or admin)."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_builtin and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only admins can modify builtin templates")
    if not template.is_builtin and template.created_by != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not your template")

    for field in body.model_fields_set:
        setattr(template, field, getattr(body, field))

    await db.commit()
    return _template_to_dict(template)


@router.post("/{template_id}/publish")
async def publish_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Publish a template so users can see and start agents from it (admin only)."""
    from app.models.user import UserRole
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only admins can publish templates")

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.is_published = True
    template.published_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info(f"Template {template_id} published by {user.id}")
    return _template_to_dict(template)


@router.post("/{template_id}/unpublish")
async def unpublish_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Unpublish a template — hides it from users (admin only)."""
    from app.models.user import UserRole
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only admins can unpublish templates")

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.is_published = False
    template.published_at = None
    await db.commit()
    logger.info(f"Template {template_id} unpublished by {user.id}")
    return _template_to_dict(template)


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a template (builtin templates cannot be deleted)."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_builtin:
        raise HTTPException(status_code=400, detail="Cannot delete builtin templates")
    if template.created_by != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not your template")

    await db.delete(template)
    await db.commit()
    return {"deleted": True}


@router.post("/{template_id}/create-agent")
async def create_agent_from_template(
    template_id: int,
    body: CreateFromTemplate,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Create an agent from a template. Users can only start from published templates."""
    from app.models.user import UserRole

    template = await db.scalar(
        select(AgentTemplate).where(AgentTemplate.id == template_id)
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Users can only start from published templates
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER) and not template.is_published:
        raise HTTPException(status_code=403, detail="This template is not published yet")

    agent_name = body.name or template.display_name
    uid = user.id if user.id != "__anonymous__" else None

    manager = AgentManager(db, docker, redis)

    try:
        agent = await manager.create_agent(
            name=agent_name,
            model=template.model,
            role=template.role,
            integrations=template.integrations or [],
            permissions=template.permissions or [],
            user_id=uid,
        )

        if template.claude_md and agent.container_id:
            try:
                docker.write_file_in_container(
                    agent.container_id, "/workspace/CLAUDE.md", template.claude_md
                )
            except Exception as e:
                logger.warning(f"Failed to write template CLAUDE.md: {e}")

        if template.knowledge_template and agent.container_id:
            try:
                docker.write_file_in_container(
                    agent.container_id, "/workspace/knowledge.md", template.knowledge_template
                )
                agent.config = {
                    **agent.config,
                    "onboarding_complete": True,
                    "knowledge_template": template.knowledge_template,
                }
                await db.commit()
            except Exception as e:
                logger.warning(f"Failed to write knowledge template: {e}")

        # Store template origin on agent
        from app.models.agent import Agent
        from sqlalchemy import update
        await db.execute(
            update(Agent).where(Agent.id == agent.id).values(template_id=template.id)
        )
        await db.commit()

        metrics = await manager.get_agent_with_metrics(agent.id)
        return {**metrics, "template_id": template.id, "template_name": template.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
