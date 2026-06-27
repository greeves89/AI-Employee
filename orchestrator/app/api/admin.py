"""Admin API - aggregated stats, system overview, and agent assignments (admin-only)."""

import logging

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

logger = logging.getLogger(__name__)

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
    # Sanitize name for Docker container compatibility
    raw_name = body.name or f"{template.display_name}-{target_user.name}"
    agent_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in raw_name).strip()
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


# --- Distribute a trained agent as per-user clones ---


class DistributeAgentRequest(BaseModel):
    source_agent_id: str           # the "trained"/finished agent to clone
    user_ids: list[str] = []       # explicit target users
    role_id: int | None = None     # custom role → every active member gets a copy
    name_prefix: str | None = None


def _sanitize_agent_name(raw: str) -> str:
    return "".join(c if c.isalnum() or c in "-_ " else "" for c in raw).strip() or "Agent"


@router.post("/distribute-agent")
async def distribute_agent(
    body: DistributeAgentRequest,
    user=Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Clone a fully-trained source agent into an independent per-user copy for
    each target user (explicit list + all active members of a custom role).

    Each copy is its own agent — own container, own workspace volume, owned by
    the target user — never a shared instance. The copy inherits the source's
    full config (model, mode, llm_config/ai_account, role, permissions,
    integrations, MCP servers, budget, autonomy, browser) AND a clone of its
    workspace (knowledge.md, installed skills, CLAUDE.md, docs — the trained
    brain). Snapshot semantics + idempotent: a user who already holds a copy of
    this source is skipped.
    """
    from app.core.agent_manager import AgentManager

    source = await db.get(Agent, body.source_agent_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source agent not found")

    # Resolve target user ids: explicit + active members of the role.
    target_ids: set[str] = {u for u in body.user_ids if u}
    if body.role_id is not None:
        members = (await db.execute(
            select(User.id).where(User.custom_role_id == body.role_id, User.is_active == True)  # noqa: E712
        )).scalars().all()
        target_ids |= set(members)
    if not target_ids:
        raise HTTPException(status_code=400, detail="No target users (pass user_ids and/or a role_id with members)")

    # Decrypt the source's per-agent llm_config api key so the clone is faithful
    # (re-encrypted per copy by create_agent). Sources using the global custom_llm
    # config (llm_config is null) just clone as null and reuse the global config.
    src_llm = dict(source.llm_config) if source.llm_config else None
    if src_llm and src_llm.get("api_key_encrypted") and not src_llm.get("api_key"):
        from app.core.encryption import decrypt_token
        try:
            src_llm["api_key"] = decrypt_token(src_llm["api_key_encrypted"])
        except Exception:
            pass
        src_llm.pop("api_key_encrypted", None)

    cfg = source.config or {}
    manager = AgentManager(db, docker, redis)
    created: list[dict] = []
    skipped: list[dict] = []

    for uid in target_ids:
        target_user = await db.get(User, uid)
        if not target_user or not target_user.is_active:
            skipped.append({"user_id": uid, "reason": "user not found or inactive"})
            continue
        # Idempotent: one copy of a given source per user.
        existing = await db.scalar(
            select(Agent.id).where(Agent.source_agent_id == source.id, Agent.user_id == uid)
        )
        if existing:
            skipped.append({"user_id": uid, "user_name": target_user.name, "reason": "already has a copy", "agent_id": existing})
            continue

        name = _sanitize_agent_name(f"{(body.name_prefix or source.name)} - {target_user.name}")
        try:
            clone = await manager.create_agent(
                name=name,
                model=source.model,
                role=cfg.get("role", ""),
                integrations=cfg.get("integrations", []),
                permissions=cfg.get("permissions", []),
                user_id=uid,
                budget_usd=source.budget_usd,
                budget_exceeded_action=source.budget_exceeded_action,
                mode=source.mode,
                llm_config=src_llm,
                ai_account_id=source.ai_account_id,
                browser_mode=source.browser_mode,
                autonomy_level=source.autonomy_level,
            )
            clone.source_agent_id = source.id
            clone.template_id = source.template_id
            # Carry MCP-server grants (skills travel with the workspace copy).
            new_cfg = clone.config or {}
            new_cfg["mcp_servers"] = cfg.get("mcp_servers", [])
            clone.config = new_cfg
            await db.commit()

            # Clone the trained workspace into the fresh copy, then restart so the
            # container picks up the copied brain + rebuilt MCP env cleanly.
            if source.volume_name and clone.volume_name:
                try:
                    docker.copy_workspace_volume(source.volume_name, clone.volume_name)
                    await manager.restart_agent(clone.id)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Workspace clone/restart for {clone.id} failed: {e}")

            db.add(AgentAccess(agent_id=clone.id, user_id=uid))
            await db.commit()
            created.append({"user_id": uid, "user_name": target_user.name, "agent_id": clone.id, "agent_name": clone.name})
        except Exception as e:  # noqa: BLE001
            logger.exception(f"Failed to clone source {source.id} for user {uid}")
            skipped.append({"user_id": uid, "user_name": target_user.name, "reason": f"error: {e}"})

    return {
        "status": "distributed",
        "source_agent_id": source.id,
        "source_agent_name": source.name,
        "created": created,
        "skipped": skipped,
        "created_count": len(created),
        "skipped_count": len(skipped),
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
            "source_agent_id": getattr(agent, "source_agent_id", None),
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
