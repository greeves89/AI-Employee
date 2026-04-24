import asyncio
import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.agent_manager import AgentManager
from app.core.file_manager import FileManager
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth, require_auth_or_agent, require_manager, verify_agent_token
from app.models.agent import Agent, AgentState
from app.security.agent_guard import check_inter_agent_message, notify_security_block
from app.models.chat_message import ChatMessage
from app.schemas.agent import AgentCreate, AgentListResponse, AgentModelUpdate, AgentResponse, KnowledgeResponse, KnowledgeUpdate, LLMConfigResponse, LLMConfigUpdate
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/agents", tags=["agents"])


def _get_agent_manager(
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
) -> AgentManager:
    return AgentManager(db, docker, redis)


def _get_file_manager(
    docker: DockerService = Depends(get_docker_service),
) -> FileManager:
    return FileManager(docker)


async def _check_owner(agent_id: str, user, db: AsyncSession) -> None:
    """Verify user has access to the agent. Raises 403 if not."""
    from app.dependencies import require_agent_access
    await require_agent_access(agent_id, user, db)


@router.get("/permissions")
async def get_permission_packages(user=Depends(require_auth)):
    """List available permission packages for agent creation."""
    from app.core.agent_manager import PERMISSION_PACKAGES, DEFAULT_PERMISSIONS
    packages = [
        {
            "id": pkg_id,
            "label": pkg["label"],
            "description": pkg["description"],
            "icon": pkg["icon"],
            "default": pkg_id in DEFAULT_PERMISSIONS,
        }
        for pkg_id, pkg in PERMISSION_PACKAGES.items()
    ]
    return {"packages": packages, "defaults": DEFAULT_PERMISSIONS}


# --- Team routes (MUST be before /{agent_id} to avoid path conflicts) ---


@router.get("/team/directory")
async def get_team_directory(
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get the team directory - all agents with their roles and status."""
    from app.models.user import UserRole
    agents = await manager.list_agents()
    if hasattr(user, "role") and user.role != UserRole.ADMIN:
        from app.models.agent_access import AgentAccess
        access_result = await db.execute(
            select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
        )
        accessible_ids = {row[0] for row in access_result.all()}
        agents = [
            a for a in agents
            if a.user_id is None or a.user_id == user.id or a.id in accessible_ids
        ]
    directory = []
    for agent in agents:
        config = agent.config or {}
        directory.append({
            "id": agent.id,
            "name": agent.name,
            "role": config.get("role", ""),
            "onboarding_complete": config.get("onboarding_complete", False),
            "state": agent.state.value if hasattr(agent.state, "value") else str(agent.state),
            "model": agent.model,
        })
    return {"agents": directory, "total": len(directory)}


@router.get("/team/messages")
async def get_agent_messages(
    minutes: int = 60,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get recent inter-agent messages for visualization."""
    from datetime import timedelta
    from sqlalchemy import select

    from app.models.agent_message import AgentMessage as AgentMessageModel

    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    # Higher limit for longer time windows to get accurate connection counts
    fetch_limit = 2000 if minutes > 1440 else 500 if minutes > 360 else 100

    result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.timestamp >= since)
        .order_by(AgentMessageModel.timestamp.desc())
        .limit(fetch_limit)
    )
    messages = result.scalars().all()

    connections: dict[str, dict] = {}
    recent_bubbles: list[dict] = []

    for msg in messages:
        pair = tuple(sorted([msg.from_agent_id, msg.to_agent_id]))
        key = f"{pair[0]}:{pair[1]}"
        if key not in connections:
            connections[key] = {
                "from": pair[0],
                "to": pair[1],
                "count": 0,
                "last_at": msg.timestamp.isoformat(),
            }
        connections[key]["count"] += 1

    for msg in messages[:10]:
        recent_bubbles.append({
            "from": msg.from_agent_id,
            "to": msg.to_agent_id,
            "text": msg.text[:60] + ("..." if len(msg.text) > 60 else ""),
            "from_name": msg.from_agent_name,
            "timestamp": msg.timestamp.isoformat(),
            "message_id": msg.message_id,
            "message_type": msg.message_type,
            "reply_to": msg.reply_to,
        })

    return {
        "connections": list(connections.values()),
        "messages": recent_bubbles,
        "total": len(messages),
    }


@router.get("/team/conversation")
async def get_agent_conversation(
    agent_a: str,
    agent_b: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get the full conversation between two specific agents."""
    from sqlalchemy import select, or_, and_

    from app.models.agent_message import AgentMessage as AgentMessageModel

    result = await db.execute(
        select(AgentMessageModel)
        .where(
            or_(
                and_(
                    AgentMessageModel.from_agent_id == agent_a,
                    AgentMessageModel.to_agent_id == agent_b,
                ),
                and_(
                    AgentMessageModel.from_agent_id == agent_b,
                    AgentMessageModel.to_agent_id == agent_a,
                ),
            )
        )
        .order_by(AgentMessageModel.timestamp.asc())
        .limit(200)
    )
    messages = result.scalars().all()

    return {
        "messages": [
            {
                "from_id": msg.from_agent_id,
                "from_name": msg.from_agent_name,
                "to_id": msg.to_agent_id,
                "text": msg.text,
                "timestamp": msg.timestamp.isoformat(),
                "message_id": msg.message_id,
                "message_type": msg.message_type,
                "reply_to": msg.reply_to,
            }
            for msg in messages
        ],
        "total": len(messages),
    }


@router.get("/team/poll-reply")
async def poll_reply(
    from_agent_id: str = "",
    to_agent_id: str = "",
    since_id: int = 0,
    timeout: int = 45,
    db: AsyncSession = Depends(get_db),
    agent=Depends(verify_agent_token),
):
    """Poll for a new message from a specific agent. Long-polls up to timeout seconds.

    Requires a valid agent token. The requesting agent must be the recipient (to_agent_id).
    """
    if agent["agent_id"] != to_agent_id:
        raise HTTPException(status_code=403, detail="Agents may only poll their own messages")
    import asyncio
    from sqlalchemy import select, and_
    from app.models.agent_message import AgentMessage as AgentMessageModel

    timeout = min(timeout, 60)  # cap at 60s
    poll_interval = 2  # check every 2 seconds
    elapsed = 0

    while elapsed < timeout:
        query = (
            select(AgentMessageModel)
            .where(and_(
                AgentMessageModel.from_agent_id == from_agent_id,
                AgentMessageModel.to_agent_id == to_agent_id,
                AgentMessageModel.id > since_id,
            ))
            .order_by(AgentMessageModel.id.desc())
            .limit(1)
        )
        result = await db.execute(query)
        msg = result.scalar_one_or_none()

        if msg:
            return {
                "found": True,
                "message": {
                    "id": msg.id,
                    "message_id": msg.message_id,
                    "from_agent_id": msg.from_agent_id,
                    "from_name": msg.from_agent_name,
                    "text": msg.text,
                    "message_type": msg.message_type,
                    "timestamp": msg.timestamp.isoformat(),
                },
            }

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    return {"found": False, "message": None}


# --- End team routes ---


@router.get("/", response_model=AgentListResponse)
async def list_agents(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    from app.models.user import UserRole

    agents = await manager.list_agents()
    # Non-admins only see their own agents (+ unowned + shared via AgentAccess)
    if user.role != UserRole.ADMIN:
        from app.models.agent_access import AgentAccess
        access_result = await db.execute(
            select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
        )
        accessible_ids = {row[0] for row in access_result.all()}
        agents = [
            a for a in agents
            if a.user_id is None or a.user_id == user.id or a.id in accessible_ids
        ]
    # Run sequentially — AsyncSession does not support concurrent queries
    # on the same connection (asyncpg: "another operation is in progress").
    metrics_list = []
    for agent in agents:
        metrics_list.append(await manager.get_agent_with_metrics(agent.id, include_stats=False))
    agent_responses = [AgentResponse(**m) for m in metrics_list]
    return AgentListResponse(agents=agent_responses, total=len(agent_responses))


@router.post("/", response_model=AgentResponse, status_code=201)
async def create_agent(
    data: AgentCreate,
    user=Depends(require_manager),
    manager: AgentManager = Depends(_get_agent_manager),
):
    try:
        # Validate: custom_llm mode requires llm_config
        if data.mode == "custom_llm" and not data.llm_config:
            raise HTTPException(
                status_code=422,
                detail="llm_config is required when mode is 'custom_llm'",
            )

        # Don't set user_id for anonymous (setup mode) users
        uid = user.id if user.id != "__anonymous__" else None
        agent = await manager.create_agent(
            name=data.name, model=data.model, role=data.role,
            integrations=data.integrations, permissions=data.permissions,
            user_id=uid, budget_usd=data.budget_usd,
            mode=data.mode,
            llm_config=data.llm_config.model_dump() if data.llm_config else None,
            browser_mode=data.browser_mode,
            autonomy_level=data.autonomy_level,
        )
        metrics = await manager.get_agent_with_metrics(agent.id)
        return AgentResponse(**metrics)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await _check_owner(agent_id, user, db)
    try:
        return await manager.get_agent_with_metrics(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class AutonomyLevelUpdate(BaseModel):
    level: str


@router.post("/{agent_id}/autonomy-level")
async def set_autonomy_level(
    agent_id: str,
    data: AutonomyLevelUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _check_owner(agent_id, user, db)
    level = data.level.lower()
    if level not in {"l1", "l2", "l3", "l4"}:
        raise HTTPException(status_code=422, detail="Invalid level. Must be l1, l2, l3, or l4")
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    previous_level = agent.autonomy_level
    agent.autonomy_level = level
    await db.commit()

    # Apply the matching approval rule preset for this level
    from app.api.approval_rules import apply_autonomy_preset
    rules = await apply_autonomy_preset(db, agent_id, level)

    # Audit log
    from app.models.audit_log import AuditLog, AuditEventType
    db.add(AuditLog(
        agent_id=agent_id,
        event_type=AuditEventType.AUTONOMY_LEVEL_CHANGED,
        command=f"autonomy_level: {previous_level} → {level}",
        outcome="success",
        user_id=str(user.id),
        meta={"previous_level": previous_level, "new_level": level, "rules_applied": [r.name for r in rules]},
    ))
    await db.commit()

    return {
        "agent_id": agent.id,
        "autonomy_level": agent.autonomy_level,
        "rules_applied": len(rules),
        "rule_names": [r.name for r in rules],
    }


@router.post("/{agent_id}/stop")
async def stop_agent(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager.stop_agent(agent_id)
        return {"status": "stopped", "agent_id": agent.id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/start")
async def start_agent(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager.start_agent(agent_id)
        return {"status": "started", "agent_id": agent.id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/restart")
async def restart_agent(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Restart agent with fresh config (picks up new MCP servers, integrations, etc)."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager.restart_agent(agent_id)
        metrics = await manager.get_agent_with_metrics(agent.id)
        return AgentResponse(**metrics)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/update")
async def update_agent(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update agent to latest container image, preserving all data."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager.update_agent(agent_id)
        metrics = await manager.get_agent_with_metrics(agent.id)
        return AgentResponse(**metrics)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{agent_id}/llm-config")
async def update_llm_config(
    agent_id: str,
    body: LLMConfigUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update the LLM configuration for a custom_llm agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if agent.mode != "custom_llm":
            raise HTTPException(status_code=400, detail="Agent is not in custom_llm mode")

        updated = await manager.update_llm_config(agent_id, body.model_dump(exclude_none=True))
        return {"agent_id": agent_id, "llm_config": updated}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.patch("/{agent_id}/model")
async def update_agent_model(
    agent_id: str,
    body: AgentModelUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update the model provider and model for an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        agent.model = body.model
        config = dict(agent.config or {})
        config["model_provider"] = body.model_provider
        agent.config = config
        await db.commit()
        await db.refresh(agent)

        # Restart container to pick up new model
        if agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            try:
                await manager.restart_agent(agent_id)
            except Exception:
                pass  # Non-critical — next task will use new model

        return {
            "agent_id": agent_id,
            "model": agent.model,
            "model_provider": body.model_provider,
            "status": "updated",
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class AgentBudgetUpdate(BaseModel):
    budget_usd: float | None  # None = unlimited


@router.patch("/{agent_id}/budget")
async def update_agent_budget(
    agent_id: str,
    body: AgentBudgetUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set or clear the monthly budget cap for an agent (admin or owner)."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        agent.budget_usd = body.budget_usd
        await db.commit()
        return {"agent_id": agent_id, "budget_usd": agent.budget_usd, "status": "updated"}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.delete("/{agent_id}")
async def remove_agent(
    agent_id: str,
    remove_data: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await _check_owner(agent_id, user, db)
    try:
        await manager.remove_agent(agent_id, remove_data=remove_data)
        return {"status": "removed", "agent_id": agent_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/stats")
async def agent_stats(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await _check_owner(agent_id, user, db)
    try:
        return await manager.get_agent_with_metrics(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/knowledge", response_model=KnowledgeResponse)
async def get_agent_knowledge(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Read the agent's knowledge.md knowledge base."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        try:
            _, content = docker.exec_in_container(
                agent.container_id, "cat /workspace/knowledge.md"
            )
        except Exception:
            content = ""

        config = agent.config or {}
        return KnowledgeResponse(
            knowledge=content,
            metrics=config.get("metrics", {}),
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.put("/{agent_id}/knowledge")
async def update_agent_knowledge(
    agent_id: str,
    body: KnowledgeUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Update the agent's knowledge.md knowledge base."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        docker.write_file_in_container(
            agent.container_id, "/workspace/knowledge.md", body.content
        )
        return {"status": "updated", "agent_id": agent_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/files/upload")
async def upload_files(
    agent_id: str,
    path: str = "/workspace",
    files: list[UploadFile] = File(...),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    file_mgr: FileManager = Depends(_get_file_manager),
):
    """Upload files to an agent's workspace."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        file_data: list[tuple[str, bytes]] = []
        for f in files:
            content = await f.read()
            file_data.append((f.filename or "unnamed", content))

        uploaded = await file_mgr.upload_files(agent.container_id, path, file_data)
        return {"uploaded": uploaded, "path": path}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{agent_id}/files")
async def browse_files(
    agent_id: str,
    path: str = "/workspace",
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    file_mgr: FileManager = Depends(_get_file_manager),
):
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")
        return {"path": path, "entries": file_mgr.list_directory(agent.container_id, path)}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/files/download")
async def download_file(
    agent_id: str,
    path: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    file_mgr: FileManager = Depends(_get_file_manager),
):
    from fastapi.responses import Response

    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")
        content = file_mgr.read_file(agent.container_id, path)
        filename = path.split("/")[-1]
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{agent_id}/files")
async def delete_file(
    agent_id: str,
    path: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Delete a file or directory in the agent's workspace."""
    await _check_owner(agent_id, user, db)
    agent = await manager._get_agent(agent_id)
    if not agent.container_id:
        raise HTTPException(status_code=400, detail="Agent has no container")
    # Safety: only allow deletion within /workspace
    if not path.startswith("/workspace/") or ".." in path:
        raise HTTPException(status_code=400, detail="Path must be inside /workspace/")
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()
        container = client.containers.get(agent.container_id)
        exit_code, output = container.exec_run(["rm", "-rf", path])
        if exit_code != 0:
            raise HTTPException(status_code=500, detail=output.decode())
        return {"deleted": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AgentMessage(BaseModel):
    from_agent_id: str | None = None
    from_name: str | None = None
    text: str
    message_type: str | None = "message"  # message, question, response, handoff, notification, status_update
    reply_to: str | None = None  # message_id of message being replied to


@router.post("/{agent_id}/message")
async def send_message_to_agent(
    agent_id: str,
    body: AgentMessage,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    redis: RedisService = Depends(get_redis_service),
):
    """Send a message to an agent's chat queue (for inter-agent or external messaging)."""
    # Skip owner check for agent-to-agent messages
    if getattr(user, "role", "") != "agent":
        await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        # --- AgentGuard: Check inter-agent messages for injection ---
        from_id = body.from_agent_id or "external"
        verdict = check_inter_agent_message(body.text, from_agent=from_id, to_agent=agent_id)
        if not verdict.allowed:
            await notify_security_block(
                redis.client,
                source=f"inter-agent/{from_id}",
                reason=verdict.reason,
                agent_id=agent_id,
            )
            raise HTTPException(
                status_code=403,
                detail=f"Blocked by AgentGuard: {verdict.reason}",
            )

        # Rate-limit agent-to-agent messages: 20/min per (from, to) pair
        if getattr(user, "role", "") == "agent" and body.from_agent_id:
            rate_key = f"rate:msg:{body.from_agent_id}:{agent_id}"
            current = await redis.client.incr(rate_key)
            if current == 1:
                await redis.client.expire(rate_key, 60)
            if current > 20:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: max 20 messages/min from {body.from_agent_id} to {agent_id}",
                )

        # Push to dedicated inter-agent message queue
        sender = body.from_name or body.from_agent_id or "System"
        from_id = body.from_agent_id or "external"

        message_id = uuid.uuid4().hex[:12]
        message_payload = json.dumps({
            "id": message_id,
            "from_agent_id": from_id,
            "from_name": sender,
            "text": body.text,
            "to_agent_id": agent_id,
            "message_type": body.message_type or "message",
            "reply_to": body.reply_to,
        })
        await redis.client.lpush(f"agent:{agent_id}:messages", message_payload)

        # Persist in DB for history/visualization
        from app.models.agent_message import AgentMessage as AgentMessageModel
        db_msg = AgentMessageModel(
            message_id=message_id,
            from_agent_id=from_id,
            from_agent_name=sender,
            to_agent_id=agent_id,
            text=body.text,
            message_type=body.message_type or "message",
            reply_to=body.reply_to,
        )
        db.add(db_msg)
        await db.commit()

        # Publish event for real-time frontend updates
        await redis.client.publish("agent:messages", json.dumps({
            "id": message_id,
            "from_agent_id": from_id,
            "from_name": sender,
            "to_agent_id": agent_id,
            "text": body.text[:100],
        }))

        return {"status": "sent", "message_id": message_id, "to_agent": agent_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/chat/sessions")
async def get_chat_sessions(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all chat sessions for an agent."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy import func, distinct
    query = (
        select(
            ChatMessage.session_id,
            func.min(ChatMessage.timestamp).label("started_at"),
            func.max(ChatMessage.timestamp).label("last_message_at"),
            func.count(ChatMessage.id).label("message_count"),
        )
        .where(ChatMessage.agent_id == agent_id)
        .group_by(ChatMessage.session_id)
        .order_by(func.max(ChatMessage.timestamp).desc())
    )
    result = await db.execute(query)
    sessions = result.all()

    # Get first user message per session for preview
    previews = {}
    for s in sessions:
        preview_q = (
            select(ChatMessage.content)
            .where(ChatMessage.agent_id == agent_id)
            .where(ChatMessage.session_id == s.session_id)
            .where(ChatMessage.role == "user")
            .order_by(ChatMessage.id.asc())
            .limit(1)
        )
        r = await db.execute(preview_q)
        row = r.scalar_one_or_none()
        previews[s.session_id] = (row or "")[:80]

    # Filter out phantom sessions (no user messages, only empty assistant entries)
    valid_sessions = [
        s for s in sessions
        if previews.get(s.session_id) or s.message_count > 1
    ]
    # Fall back to all sessions if filtering removed everything
    if not valid_sessions:
        valid_sessions = list(sessions)

    return {
        "sessions": [
            {
                "id": s.session_id,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
                "message_count": s.message_count,
                "preview": previews.get(s.session_id, ""),
            }
            for s in valid_sessions
        ],
    }


@router.get("/{agent_id}/chat/history")
async def get_chat_history(
    agent_id: str,
    session_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    before_id: int | None = Query(None),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get chat history for an agent, optionally filtered by session."""
    await _check_owner(agent_id, user, db)
    query = select(ChatMessage).where(ChatMessage.agent_id == agent_id)
    if session_id is not None:
        query = query.where(ChatMessage.session_id == session_id)
    if before_id is not None:
        query = query.where(ChatMessage.id < before_id)
    query = query.order_by(ChatMessage.id.desc()).limit(limit)
    result = await db.execute(query)
    messages = result.scalars().all()
    # Return in chronological order (oldest first)
    messages = list(reversed(messages))
    return {
        "messages": [
            {
                "id": msg.message_id or str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "toolCalls": msg.tool_calls,
                "meta": msg.meta,
                "sessionId": msg.session_id,
            }
            for msg in messages
        ],
        "has_more": len(messages) == limit,
    }


@router.delete("/{agent_id}/chat/sessions/{session_id}")
async def delete_chat_session(
    agent_id: str,
    session_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete all messages in a chat session."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy import delete as sql_delete
    result = await db.execute(
        sql_delete(ChatMessage)
        .where(ChatMessage.agent_id == agent_id)
        .where(ChatMessage.session_id == session_id)
    )
    await db.commit()
    return {"deleted": result.rowcount}


class PermissionsUpdate(BaseModel):
    permissions: list[str]


@router.patch("/{agent_id}/permissions")
async def update_agent_permissions(
    agent_id: str,
    body: PermissionsUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update the sudo permission packages for a running agent."""
    await _check_owner(agent_id, user, db)
    from app.core.agent_manager import PERMISSION_PACKAGES, generate_sudoers
    from sqlalchemy.orm.attributes import flag_modified

    # Validate package names
    for perm in body.permissions:
        if perm not in PERMISSION_PACKAGES:
            raise HTTPException(status_code=400, detail=f"Unknown permission package: {perm}")

    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        config["permissions"] = body.permissions
        agent.config = config
        flag_modified(agent, "config")
        await db.commit()

        # Apply to running container
        if agent.container_id:
            try:
                manager._apply_permissions(agent.container_id, body.permissions)
            except Exception as e:
                return {
                    "agent_id": agent_id,
                    "permissions": body.permissions,
                    "warning": f"Saved but could not apply to container: {e}. Restart agent to apply.",
                }

        return {"agent_id": agent_id, "permissions": body.permissions}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class BrowserModeUpdate(BaseModel):
    browser_mode: bool


@router.patch("/{agent_id}/browser-mode")
async def update_agent_browser_mode(
    agent_id: str,
    body: BrowserModeUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Enable or disable Playwright browser control for an agent. Takes effect on next restart."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        agent.browser_mode = body.browser_mode
        await db.commit()
        note = " Restart the agent for the change to take effect." if agent.container_id else ""
        return {"browser_mode": body.browser_mode, "note": note.strip()}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class AgentResourceLimits(BaseModel):
    idle_timeout_minutes: int | None = None   # 0 = never stop
    workspace_size_gb: float | None = None    # disk quota override


@router.patch("/{agent_id}/resource-limits")
async def update_agent_resource_limits(
    agent_id: str,
    body: AgentResourceLimits,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update per-agent idle timeout and workspace disk quota."""
    await _check_owner(agent_id, user, db)
    agent = await manager._get_agent(agent_id)
    config = dict(agent.config or {})
    if body.idle_timeout_minutes is not None:
        config["idle_timeout_minutes"] = body.idle_timeout_minutes
    if body.workspace_size_gb is not None:
        config["workspace_size_gb"] = body.workspace_size_gb
    agent.config = config
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(agent, "config")
    await db.commit()
    return {"idle_timeout_minutes": config.get("idle_timeout_minutes"), "workspace_size_gb": config.get("workspace_size_gb")}


@router.get("/{agent_id}/integrations")
async def get_agent_integrations(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get the list of enabled integrations for an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        return {"agent_id": agent_id, "integrations": config.get("integrations", [])}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.patch("/{agent_id}/integrations")
async def update_agent_integrations(
    agent_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update the enabled integrations for an agent and restart to apply."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        old_integrations = set(config.get("integrations", []))
        new_integrations = body.get("integrations", [])
        config["integrations"] = new_integrations
        agent.config = config
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()

        # Auto-restart running agents so new tokens are injected
        if set(new_integrations) != old_integrations and agent.state == AgentState.RUNNING:
            await manager.restart_agent(agent_id)

        return {"agent_id": agent_id, "integrations": config["integrations"]}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# --- Per-Agent MCP Servers ---


@router.get("/{agent_id}/mcp-servers")
async def get_agent_mcp_servers(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get the list of MCP server IDs assigned to this agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        return {"agent_id": agent_id, "mcp_servers": config.get("mcp_servers", None)}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.patch("/{agent_id}/mcp-servers")
async def update_agent_mcp_servers(
    agent_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update which MCP servers this agent uses. Pass null to use all enabled servers."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        mcp_servers = body.get("mcp_servers")
        if mcp_servers is None:
            config.pop("mcp_servers", None)
        else:
            config["mcp_servers"] = mcp_servers
        agent.config = config
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()

        # Auto-restart running agents so new MCP servers are registered
        if agent.state == AgentState.RUNNING:
            await manager.restart_agent(agent_id)

        return {"agent_id": agent_id, "mcp_servers": config.get("mcp_servers", None)}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# --- Volume Mounts ---


@router.get("/{agent_id}/mounts")
async def get_agent_mounts(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Return the mount labels currently assigned to this agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        return {"agent_id": agent_id, "mounts": config.get("mounts", [])}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.patch("/{agent_id}/mounts")
async def update_agent_mounts(
    agent_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Assign volume mounts to an agent (labels from admin catalog) and restart."""
    await _check_owner(agent_id, user, db)
    from app.core.mounts import parse_mount_catalog
    catalog = parse_mount_catalog(settings.agent_mount_catalog)
    new_mounts: list[str] = body.get("mounts", [])
    unknown = [m for m in new_mounts if m not in catalog]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown mount labels: {unknown}")
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        old_mounts = set(config.get("mounts", []))
        config["mounts"] = new_mounts
        agent.config = config
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()

        # Auto-restart if mounts changed and agent is running
        if set(new_mounts) != old_mounts and agent.state == AgentState.RUNNING:
            await manager.restart_agent(agent_id)

        return {"agent_id": agent_id, "mounts": new_mounts}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# --- Proactive Mode ---

class ProactiveUpdate(BaseModel):
    enabled: bool = True
    interval_seconds: int = 3600
    prompt: str | None = None


@router.get("/{agent_id}/proactive")
async def get_proactive_config(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get proactive mode config and schedule stats for an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        proactive = config.get("proactive", {})
        schedule_id = proactive.get("schedule_id")

        schedule_stats = None
        if schedule_id:
            from app.models.schedule import Schedule
            result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
            schedule = result.scalar_one_or_none()
            if schedule:
                schedule_stats = {
                    "enabled": schedule.enabled,
                    "interval_seconds": schedule.interval_seconds,
                    "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                    "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
                    "total_runs": schedule.total_runs,
                    "success_count": schedule.success_count,
                    "fail_count": schedule.fail_count,
                }

        return {
            "agent_id": agent_id,
            "proactive": proactive,
            "schedule": schedule_stats,
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/proactive")
async def update_proactive_config(
    agent_id: str,
    body: ProactiveUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Enable, disable, or update proactive mode for an agent."""
    await _check_owner(agent_id, user, db)
    from datetime import timedelta, timezone as tz
    from app.models.schedule import Schedule
    from app.core.agent_manager import PROACTIVE_PROMPT
    from sqlalchemy.orm.attributes import flag_modified

    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        proactive = config.get("proactive", {})
        schedule_id = proactive.get("schedule_id")
        now = datetime.now(tz.utc)

        if schedule_id:
            result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
            schedule = result.scalar_one_or_none()
            if schedule:
                schedule.enabled = body.enabled
                schedule.interval_seconds = body.interval_seconds
                if body.prompt:
                    schedule.prompt = body.prompt
                if body.enabled:
                    schedule.next_run_at = now + timedelta(seconds=body.interval_seconds)
            else:
                schedule_id = None

        if not schedule_id:
            schedule_id = uuid.uuid4().hex[:8]
            schedule = Schedule(
                id=schedule_id,
                name=f"[Proactive] {agent.name}",
                prompt=body.prompt or PROACTIVE_PROMPT,
                interval_seconds=body.interval_seconds,
                priority=0,
                agent_id=agent_id,
                enabled=body.enabled,
                next_run_at=now + timedelta(seconds=body.interval_seconds),
            )
            db.add(schedule)

        proactive = {
            "enabled": body.enabled,
            "schedule_id": schedule_id,
            "interval_seconds": body.interval_seconds,
        }
        config["proactive"] = proactive
        agent.config = config
        flag_modified(agent, "config")
        await db.commit()

        return {"agent_id": agent_id, "proactive": proactive, "status": "updated"}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.delete("/{agent_id}/proactive")
async def delete_proactive_config(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Disable and remove proactive mode for an agent."""
    await _check_owner(agent_id, user, db)
    from app.models.schedule import Schedule
    from sqlalchemy.orm.attributes import flag_modified

    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        proactive = config.get("proactive", {})
        schedule_id = proactive.get("schedule_id")

        if schedule_id:
            result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
            schedule = result.scalar_one_or_none()
            if schedule:
                await db.delete(schedule)

        config.pop("proactive", None)
        agent.config = config
        flag_modified(agent, "config")
        await db.commit()

        return {"agent_id": agent_id, "status": "proactive_removed"}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# --- Per-Agent Telegram Bot ---


class TelegramConfigUpdate(BaseModel):
    bot_token: str


@router.get("/{agent_id}/telegram")
async def get_agent_telegram(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get Telegram bot config for an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        has_token = bool(config.get("telegram_bot_token"))
        auth_key = config.get("telegram_auth_key", "")

        # Check if bot is running
        from fastapi import Request
        bot_running = False
        try:
            from starlette.requests import Request as _  # noqa
            import app.main as main_mod
            if hasattr(main_mod, "app"):
                tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
                if tg_manager:
                    bot_running = tg_manager.is_running(agent_id)
        except Exception:
            pass

        return {
            "agent_id": agent_id,
            "has_token": has_token,
            "auth_key": auth_key if has_token else "",
            "bot_running": bot_running,
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.put("/{agent_id}/telegram")
async def set_agent_telegram(
    agent_id: str,
    body: TelegramConfigUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set Telegram bot token for an agent and start the bot."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy.orm.attributes import flag_modified
    from app.telegram.bot_manager import generate_auth_key

    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}

        # Generate auth key if not exists
        auth_key = config.get("telegram_auth_key") or generate_auth_key()
        config["telegram_bot_token"] = body.bot_token
        config["telegram_auth_key"] = auth_key
        agent.config = config
        flag_modified(agent, "config")
        await db.commit()

        # Start the bot
        bot_running = False
        try:
            import app.main as main_mod
            tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
            if tg_manager:
                await tg_manager.start_bot(agent_id, agent.name, body.bot_token, auth_key)
                bot_running = True
        except Exception as e:
            # Token may be invalid - save config but report error
            return {
                "agent_id": agent_id,
                "auth_key": auth_key,
                "bot_running": False,
                "error": str(e),
            }

        return {
            "agent_id": agent_id,
            "auth_key": auth_key,
            "bot_running": bot_running,
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.delete("/{agent_id}/telegram")
async def remove_agent_telegram(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Remove Telegram bot from an agent and stop it."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy.orm.attributes import flag_modified

    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        config.pop("telegram_bot_token", None)
        config.pop("telegram_auth_key", None)
        agent.config = config
        flag_modified(agent, "config")
        await db.commit()

        # Stop the bot
        try:
            import app.main as main_mod
            tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
            if tg_manager:
                await tg_manager.stop_bot(agent_id)
        except Exception:
            pass

        return {"agent_id": agent_id, "status": "telegram_removed"}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class TelegramSendMessage(BaseModel):
    message: str


@router.post("/{agent_id}/telegram/send")
async def send_telegram_message(
    agent_id: str,
    body: TelegramSendMessage,
):
    """Send a direct Telegram message to all authorized users of this agent.

    Called by the agent's MCP send_telegram tool (no user auth needed,
    agent authenticates via AGENT_TOKEN header).
    """
    sent_to = 0
    try:
        import app.main as main_mod
        tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
        if tg_manager:
            bot = tg_manager.get_bot(agent_id)
            if bot and bot._started:
                await bot.send_to_all_authorized(body.message)
                # Count authorized users
                import redis.asyncio as aioredis
                redis = aioredis.from_url(settings.redis_url, decode_responses=True)
                sent_to = await redis.scard(f"agent:{agent_id}:tg_auth")
                await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"agent_id": agent_id, "sent_to": sent_to}


@router.post("/{agent_id}/telegram/regenerate-key")
async def regenerate_telegram_key(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Regenerate the Telegram auth key (invalidates all current sessions)."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy.orm.attributes import flag_modified
    from app.telegram.bot_manager import generate_auth_key
    import redis.asyncio as aioredis

    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}

        if not config.get("telegram_bot_token"):
            raise HTTPException(status_code=400, detail="Telegram not configured for this agent")

        new_key = generate_auth_key()
        config["telegram_auth_key"] = new_key
        agent.config = config
        flag_modified(agent, "config")
        await db.commit()

        # Clear all authorized users in Redis
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis.delete(f"agent:{agent_id}:tg_auth")
        await redis.aclose()

        # Restart bot with new key
        try:
            import app.main as main_mod
            tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
            if tg_manager:
                await tg_manager.start_bot(
                    agent_id, agent.name, config["telegram_bot_token"], new_key
                )
        except Exception:
            pass

        return {"agent_id": agent_id, "auth_key": new_key}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# --- Skills (Claude Code SKILL.md files) ---


class SkillCreate(BaseModel):
    name: str  # skill directory name (lowercase, hyphens)
    description: str  # what the skill does
    content: str  # the instruction markdown (body of SKILL.md)


class SkillResponse(BaseModel):
    name: str
    description: str
    content: str


def _build_skill_md(name: str, description: str, content: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{content}\n"


def _parse_skill_md(raw: str) -> dict:
    """Parse a SKILL.md file into name, description, content."""
    import re
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", raw, re.DOTALL)
    if not m:
        return {"name": "", "description": "", "content": raw.strip()}
    frontmatter, body = m.group(1), m.group(2).strip()
    meta: dict[str, str] = {}
    for line in frontmatter.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return {
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "content": body,
    }


@router.get("/{agent_id}/skills", response_model=list[SkillResponse])
async def list_skills(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """List all Claude Code skills for an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        # Find all SKILL.md files (scan multiple known locations)
        try:
            _, output = docker.exec_in_container(
                agent.container_id,
                "sh -c 'find /workspace/.claude/skills /home/agent/.agents/skills /home/agent/.claude/skills -name SKILL.md 2>/dev/null || true'",
            )
        except Exception:
            return []

        paths = [p.strip() for p in output.strip().splitlines() if p.strip()]
        skills = []
        for path in paths:
            try:
                _, raw = docker.exec_in_container(
                    agent.container_id, f"cat '{path}'"
                )
                parsed = _parse_skill_md(raw)
                # Derive name from directory if not in frontmatter
                dir_name = path.split("/")[-2] if "/" in path else ""
                if not parsed["name"]:
                    parsed["name"] = dir_name
                skills.append(SkillResponse(**parsed))
            except Exception:
                continue

        # Also include DB marketplace skills assigned to this agent
        from app.models.skill import Skill, SkillStatus, AgentSkillAssignment
        from sqlalchemy import select as sa_select
        existing_names = {s.name for s in skills}
        assignments = await db.execute(
            sa_select(Skill).join(
                AgentSkillAssignment, AgentSkillAssignment.skill_id == Skill.id
            ).where(
                AgentSkillAssignment.agent_id == agent_id,
                Skill.status == SkillStatus.ACTIVE,
            )
        )
        for s in assignments.scalars().all():
            if s.name not in existing_names:
                skills.append(SkillResponse(name=s.name, description=s.description or "", content=s.content or ""))

        return skills
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/skills", response_model=SkillResponse, status_code=201)
async def create_skill(
    agent_id: str,
    body: SkillCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Create a new Claude Code skill for an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        skill_dir = f"/workspace/.claude/skills/{body.name}"
        docker.exec_in_container(agent.container_id, f"mkdir -p '{skill_dir}'")

        content = _build_skill_md(body.name, body.description, body.content)
        docker.write_file_in_container(
            agent.container_id, f"{skill_dir}/SKILL.md", content
        )

        return SkillResponse(name=body.name, description=body.description, content=body.content)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.put("/{agent_id}/skills/{skill_name}", response_model=SkillResponse)
async def update_skill(
    agent_id: str,
    skill_name: str,
    body: SkillCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Update an existing Claude Code skill."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        skill_path = f"/workspace/.claude/skills/{skill_name}/SKILL.md"

        # If renaming, remove old directory and create new one
        if body.name != skill_name:
            docker.exec_in_container(
                agent.container_id,
                f"rm -rf '/workspace/.claude/skills/{skill_name}'",
            )
            new_dir = f"/workspace/.claude/skills/{body.name}"
            docker.exec_in_container(agent.container_id, f"mkdir -p '{new_dir}'")
            skill_path = f"{new_dir}/SKILL.md"

        content = _build_skill_md(body.name, body.description, body.content)
        docker.write_file_in_container(agent.container_id, skill_path, content)

        return SkillResponse(name=body.name, description=body.description, content=body.content)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.delete("/{agent_id}/skills/{skill_name}", status_code=204)
async def delete_skill(
    agent_id: str,
    skill_name: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Delete a Claude Code skill from an agent."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")
        safe_name = skill_name.replace("'", "").replace(";", "").replace("&", "").replace("/", "")
        docker.exec_in_container(
            agent.container_id,
            f"rm -rf '/workspace/.claude/skills/{safe_name}'",
        )
        return None
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class SkillInstall(BaseModel):
    repo: Optional[str] = None  # GitHub URL or shorthand — None for DB skills
    skill: str  # Skill name within the repo
    content: Optional[str] = None  # Direct content for DB marketplace skills


@router.post("/{agent_id}/skills/install")
async def install_skill_from_repo(
    agent_id: str,
    body: SkillInstall,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Install a Claude Code skill — either from GitHub (npx) or directly from DB content."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        skill_name = body.skill.replace("'", "").replace(";", "").replace("&", "").replace("/", "-")
        skill_dir = f"/workspace/.claude/skills/{skill_name}"
        skill_path = f"{skill_dir}/SKILL.md"

        if body.content:
            # DB skill: write content directly into the agent container
            escaped = body.content.replace("'", "'\\''")
            write_cmd = f"sh -c 'mkdir -p \"{skill_dir}\" && printf \\'%s\\' \\'{escaped}\\' > \"{skill_path}\"'"
            try:
                docker.exec_in_container(agent.container_id, f"mkdir -p \"{skill_dir}\"")
                b64 = base64.b64encode(body.content.encode()).decode()
                docker.exec_in_container(
                    agent.container_id,
                    f"sh -c 'echo \"{b64}\" | base64 -d > \"{skill_path}\"'"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Skill write failed: {str(e)[:300]}")
        elif body.repo:
            # GitHub skill: use npx skills add
            install_cmd = f"sh -c 'cd /workspace && npx -y skills add {body.repo} --skill {skill_name}'"
            copy_cmd = (
                f"sh -c '"
                f"mkdir -p /workspace/.claude/skills && "
                f"src=$(find /tmp/skills-* -type d -name \"{skill_name}\" 2>/dev/null | head -1) && "
                f"if [ -n \"$src\" ] && [ ! -d \"/workspace/.claude/skills/{skill_name}\" ]; then "
                f"cp -r \"$src\" /workspace/.claude/skills/{skill_name}; fi'"
            )
            try:
                docker.exec_in_container(agent.container_id, install_cmd)
                docker.exec_in_container(agent.container_id, copy_cmd)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Skill install failed: {str(e)[:300]}")
        else:
            raise HTTPException(status_code=400, detail="Either repo or content must be provided")

        # Verify by reading the installed SKILL.md
        try:
            _, raw = docker.exec_in_container(agent.container_id, f"sh -c 'cat \"{skill_path}\"'")
            parsed = _parse_skill_md(raw)
            if not parsed["name"]:
                parsed["name"] = body.skill
            return SkillResponse(**parsed)
        except Exception:
            return SkillResponse(name=body.skill, description="Installed from marketplace", content=body.content or "")
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")




    # Old team routes removed — moved to top of file (before /{agent_id} routes)


# ---------------------------------------------------------------------------
# Webhook triggers — external systems can trigger agent tasks via a
# per-agent bearer token. Think "Zapier / n8n / Discord / Slack / your own
# app posts to this URL, agent picks it up as a task."
# ---------------------------------------------------------------------------

import hashlib
import secrets

from fastapi import Request
from pydantic import Field


class WebhookTriggerRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=20000, description="The instruction for the agent to execute.")
    title: str | None = Field(None, max_length=200, description="Optional short title for the task. Defaults to a truncated prompt.")
    metadata: dict | None = Field(None, description="Arbitrary JSON metadata forwarded from the caller (Zapier/n8n payload, Discord message context, etc.).")


class WebhookTriggerResponse(BaseModel):
    task_id: str
    status: str
    message: str


class WebhookRotateResponse(BaseModel):
    agent_id: str
    webhook_url: str
    token: str
    warning: str = "Store this token now. It will NEVER be shown again. Use it as `Authorization: Bearer <token>` on POST requests to webhook_url."


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    # Fallback: allow `?token=` query param for systems that can't set headers
    qt = request.query_params.get("token")
    return qt.strip() if qt else None


@router.post(
    "/{agent_id}/webhook/rotate",
    response_model=WebhookRotateResponse,
    status_code=201,
)
async def rotate_agent_webhook_token(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Generate (or rotate) the webhook token for an agent.

    Returns the plaintext token ONCE. The caller must store it — the server
    only keeps a SHA-256 hash of it. Calling this endpoint again invalidates
    the previous token.
    """
    await _check_owner(agent_id, user, db)

    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    plaintext = secrets.token_urlsafe(32)  # ~43 chars, cryptographically random
    agent.webhook_token_hash = _hash_token(plaintext)
    await db.commit()

    base = settings.oauth_redirect_base_url.rstrip("/") if settings.oauth_redirect_base_url else ""
    webhook_url = f"{base}/api/v1/agents/{agent_id}/webhook" if base else f"/api/v1/agents/{agent_id}/webhook"

    return WebhookRotateResponse(
        agent_id=agent_id,
        webhook_url=webhook_url,
        token=plaintext,
    )


@router.delete("/{agent_id}/webhook")
async def revoke_agent_webhook_token(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate the current webhook token without creating a new one."""
    await _check_owner(agent_id, user, db)

    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.webhook_token_hash = None
    await db.commit()
    return {"status": "revoked", "agent_id": agent_id}


@router.post(
    "/{agent_id}/webhook",
    response_model=WebhookTriggerResponse,
    status_code=202,
)
async def trigger_agent_via_webhook(
    agent_id: str,
    data: WebhookTriggerRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Fire a task at an agent from an external system.

    Authentication is via the per-agent webhook token created by
    POST /agents/{id}/webhook/rotate. Supply it either as:
      - `Authorization: Bearer <token>` header  (preferred)
      - `?token=<token>` query param            (fallback for limited clients)

    Body: {"prompt": "...", "title"?: "...", "metadata"?: {...}}

    Returns 202 Accepted with the created task_id. Poll
    GET /api/v1/tasks/{task_id} to watch progress.
    """
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing webhook token (Authorization: Bearer <token> or ?token=)")

    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        # Don't leak existence: return 401 when agent is unknown OR token is wrong.
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")

    if not agent.webhook_token_hash:
        raise HTTPException(
            status_code=401,
            detail="Webhook not configured for this agent. Call POST /agents/{id}/webhook/rotate first.",
        )

    presented = _hash_token(token)
    # Constant-time compare to avoid timing attacks
    import hmac as _hmac
    if not _hmac.compare_digest(presented, agent.webhook_token_hash):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")

    # Build the task via the standard TaskRouter so it shows up in the UI
    # and goes through normal routing/approval logic.
    from app.core.load_balancer import LoadBalancer
    from app.core.task_router import TaskRouter
    from app.services.redis_service import RedisService

    redis: RedisService = request.app.state.redis
    docker_svc: DockerService = request.app.state.docker
    lb = LoadBalancer(redis)
    router_ = TaskRouter(db, redis, lb, docker_service=docker_svc)

    title = data.title or (data.prompt[:80] + ("..." if len(data.prompt) > 80 else ""))

    try:
        task = await router_.create_and_route_task(
            title=title,
            prompt=data.prompt,
            priority=5,
            agent_id=agent_id,
            model=None,
            parent_task_id=None,
            created_by_agent=None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create task: {e}")

    return WebhookTriggerResponse(
        task_id=task.id,
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        message=f"Task queued. Poll GET /api/v1/tasks/{task.id} for progress.",
    )
