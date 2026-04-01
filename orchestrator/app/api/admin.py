"""Admin API - aggregated stats, system overview, and agent assignments (admin-only)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.agent_template import AgentTemplate
from app.models.chat_message import ChatMessage
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_admin(user=Depends(require_auth)):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@router.get("/agents/{agent_id}/stats")
async def get_agent_admin_stats(
    agent_id: str,
    user=Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed admin stats for a single agent."""
    # Agent info
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    config = agent.config or {}

    # Owner info
    owner = None
    if agent.user_id:
        owner_obj = await db.scalar(select(User).where(User.id == agent.user_id))
        if owner_obj:
            owner = {
                "id": owner_obj.id,
                "name": owner_obj.name,
                "email": owner_obj.email,
                "role": owner_obj.role.value,
            }

    # Task stats
    task_base = select(Task).where(Task.agent_id == agent_id)

    total_tasks = await db.scalar(
        select(func.count()).select_from(task_base.subquery())
    )
    completed_tasks = await db.scalar(
        select(func.count()).select_from(
            task_base.where(Task.status == TaskStatus.COMPLETED).subquery()
        )
    )
    failed_tasks = await db.scalar(
        select(func.count()).select_from(
            task_base.where(Task.status == TaskStatus.FAILED).subquery()
        )
    )
    total_cost = await db.scalar(
        select(func.coalesce(func.sum(Task.cost_usd), 0.0)).where(
            Task.agent_id == agent_id
        )
    )
    total_duration = await db.scalar(
        select(func.coalesce(func.sum(Task.duration_ms), 0)).where(
            Task.agent_id == agent_id
        )
    )
    total_turns = await db.scalar(
        select(func.coalesce(func.sum(Task.num_turns), 0)).where(
            Task.agent_id == agent_id
        )
    )

    # Chat stats
    chat_sessions = await db.scalar(
        select(func.count(func.distinct(ChatMessage.session_id))).where(
            ChatMessage.agent_id == agent_id
        )
    )
    chat_messages = await db.scalar(
        select(func.count()).select_from(
            select(ChatMessage).where(ChatMessage.agent_id == agent_id).subquery()
        )
    )

    # Recent tasks (last 10)
    recent_q = (
        select(Task)
        .where(Task.agent_id == agent_id)
        .order_by(Task.created_at.desc())
        .limit(10)
    )
    recent_result = await db.execute(recent_q)
    recent_tasks = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "cost_usd": t.cost_usd,
            "duration_ms": t.duration_ms,
            "num_turns": t.num_turns,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in recent_result.scalars().all()
    ]

    # Visibility: which users can see this agent
    # Admin sees all, owner sees own, unowned = visible to all
    visibility = []
    if agent.user_id is None:
        visibility.append({"scope": "all", "reason": "Unowned (legacy agent)"})
    else:
        visibility.append({"scope": "owner", "user": owner})
        # All admins can see
        admin_count = await db.scalar(
            select(func.count()).select_from(
                select(User).where(User.role == UserRole.ADMIN, User.is_active == True).subquery()
            )
        )
        visibility.append({"scope": "admins", "count": admin_count})

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "container_id": agent.container_id,
            "state": agent.state.value if hasattr(agent.state, "value") else str(agent.state),
            "model": agent.model,
            "role": config.get("role", ""),
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
        },
        "owner": owner,
        "stats": {
            "total_tasks": total_tasks or 0,
            "completed_tasks": completed_tasks or 0,
            "failed_tasks": failed_tasks or 0,
            "total_cost_usd": float(total_cost or 0),
            "total_duration_ms": int(total_duration or 0),
            "total_turns": int(total_turns or 0),
            "chat_sessions": chat_sessions or 0,
            "chat_messages": chat_messages or 0,
        },
        "visibility": visibility,
        "recent_tasks": recent_tasks,
    }


@router.get("/overview")
async def get_admin_overview(
    user=Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """System-wide overview stats for the admin dashboard."""
    total_users = await db.scalar(select(func.count()).select_from(User))
    active_users = await db.scalar(
        select(func.count()).select_from(
            select(User).where(User.is_active == True).subquery()
        )
    )
    total_agents = await db.scalar(select(func.count()).select_from(Agent))
    total_tasks = await db.scalar(select(func.count()).select_from(Task))
    total_cost = await db.scalar(
        select(func.coalesce(func.sum(Task.cost_usd), 0.0))
    )
    completed_tasks = await db.scalar(
        select(func.count()).select_from(
            select(Task).where(Task.status == TaskStatus.COMPLETED).subquery()
        )
    )
    failed_tasks = await db.scalar(
        select(func.count()).select_from(
            select(Task).where(Task.status == TaskStatus.FAILED).subquery()
        )
    )

    return {
        "users": {"total": total_users or 0, "active": active_users or 0},
        "agents": {"total": total_agents or 0},
        "tasks": {
            "total": total_tasks or 0,
            "completed": completed_tasks or 0,
            "failed": failed_tasks or 0,
        },
        "cost": {"total_usd": float(total_cost or 0)},
    }


# --- Agent Assignments (Multi-Tenant) ---


class AssignAgentRequest(BaseModel):
    user_id: str
    template_id: int
    name: str | None = None
    budget_usd: float | None = None


@router.post("/assign-agent")
async def assign_agent_to_user(
    body: AssignAgentRequest,
    user=Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Create a new agent from a template and assign it to a specific user."""
    from app.core.agent_manager import AgentManager

    # Validate template
    template = await db.scalar(select(AgentTemplate).where(AgentTemplate.id == body.template_id))
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Validate user
    target_user = await db.scalar(select(User).where(User.id == body.user_id))
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if not target_user.is_active:
        raise HTTPException(status_code=400, detail="User is inactive")

    # Create agent from template
    agent_name = body.name or f"{template.display_name} ({target_user.name})"
    manager = AgentManager(db, docker, redis)

    agent = await manager.create_agent(
        name=agent_name,
        model=template.model,
        role=template.role,
        integrations=template.integrations or [],
        permissions=template.permissions or [],
        user_id=body.user_id,
        budget_usd=body.budget_usd,
        mode="claude_code",
    )

    # Track template origin
    agent.template_id = body.template_id
    await db.commit()

    # Write knowledge template to workspace
    if template.knowledge_template:
        try:
            docker_svc = DockerService()
            docker_svc.exec_in_container(
                agent.container_id,
                f"mkdir -p /workspace && cat > /workspace/knowledge.md << 'KNOWLEDGE_EOF'\n{template.knowledge_template}\nKNOWLEDGE_EOF",
            )
        except Exception:
            pass  # Non-critical

    # Create AgentAccess entry
    access = AgentAccess(agent_id=agent.id, user_id=body.user_id)
    db.add(access)
    await db.commit()

    return {
        "status": "assigned",
        "agent_id": agent.id,
        "agent_name": agent.name,
        "user_id": body.user_id,
        "user_name": target_user.name,
        "template_name": template.display_name,
    }


@router.get("/assignments")
async def list_assignments(
    user=Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all agent-to-user assignments."""
    result = await db.execute(
        select(Agent, User, AgentTemplate)
        .outerjoin(User, Agent.user_id == User.id)
        .outerjoin(AgentTemplate, Agent.template_id == AgentTemplate.id)
        .where(Agent.user_id.isnot(None))
        .order_by(Agent.created_at.desc())
    )
    rows = result.all()

    assignments = []
    for agent, owner, template in rows:
        config = agent.config or {}
        assignments.append({
            "agent_id": agent.id,
            "agent_name": agent.name,
            "user_id": owner.id if owner else None,
            "user_name": owner.name if owner else "Unknown",
            "user_email": owner.email if owner else "",
            "template_id": template.id if template else None,
            "template_name": template.display_name if template else None,
            "state": agent.state.value if hasattr(agent.state, "value") else str(agent.state),
            "model": agent.model,
            "role": config.get("role", ""),
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
        })

    return {"assignments": assignments, "total": len(assignments)}


@router.delete("/assignments/{agent_id}")
async def revoke_assignment(
    agent_id: str,
    user=Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Remove an agent assignment — stops container and deletes agent."""
    from app.core.agent_manager import AgentManager

    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    manager = AgentManager(db, docker, redis)
    await manager.remove_agent(agent_id, remove_data=False)

    return {"status": "revoked", "agent_id": agent_id}
