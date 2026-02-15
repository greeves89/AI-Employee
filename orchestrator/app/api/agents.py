import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import AgentManager
from app.core.file_manager import FileManager
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service
from app.models.chat_message import ChatMessage
from app.schemas.agent import AgentCreate, AgentListResponse, AgentResponse, KnowledgeResponse, KnowledgeUpdate
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


@router.get("/", response_model=AgentListResponse)
async def list_agents(manager: AgentManager = Depends(_get_agent_manager)):
    agents = await manager.list_agents()
    # Skip heavy Docker stats for list endpoint (fetch on detail page)
    metrics_list = await asyncio.gather(
        *(manager.get_agent_with_metrics(agent.id, include_stats=False) for agent in agents)
    )
    agent_responses = [AgentResponse(**m) for m in metrics_list]
    return AgentListResponse(agents=agent_responses, total=len(agent_responses))


@router.post("/", response_model=AgentResponse, status_code=201)
async def create_agent(
    data: AgentCreate, manager: AgentManager = Depends(_get_agent_manager)
):
    try:
        agent = await manager.create_agent(name=data.name, model=data.model, role=data.role, integrations=data.integrations)
        metrics = await manager.get_agent_with_metrics(agent.id)
        return AgentResponse(**metrics)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, manager: AgentManager = Depends(_get_agent_manager)
):
    try:
        return await manager.get_agent_with_metrics(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/stop")
async def stop_agent(
    agent_id: str, manager: AgentManager = Depends(_get_agent_manager)
):
    try:
        agent = await manager.stop_agent(agent_id)
        return {"status": "stopped", "agent_id": agent.id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/start")
async def start_agent(
    agent_id: str, manager: AgentManager = Depends(_get_agent_manager)
):
    try:
        agent = await manager.start_agent(agent_id)
        return {"status": "started", "agent_id": agent.id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/update")
async def update_agent(
    agent_id: str, manager: AgentManager = Depends(_get_agent_manager)
):
    """Update agent to latest container image, preserving all data."""
    try:
        agent = await manager.update_agent(agent_id)
        metrics = await manager.get_agent_with_metrics(agent.id)
        return AgentResponse(**metrics)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{agent_id}")
async def remove_agent(
    agent_id: str,
    remove_data: bool = False,
    manager: AgentManager = Depends(_get_agent_manager),
):
    try:
        await manager.remove_agent(agent_id, remove_data=remove_data)
        return {"status": "removed", "agent_id": agent_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/stats")
async def agent_stats(
    agent_id: str, manager: AgentManager = Depends(_get_agent_manager)
):
    try:
        return await manager.get_agent_with_metrics(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/knowledge", response_model=KnowledgeResponse)
async def get_agent_knowledge(
    agent_id: str,
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Read the agent's knowledge.md knowledge base."""
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
    manager: AgentManager = Depends(_get_agent_manager),
    docker: DockerService = Depends(get_docker_service),
):
    """Update the agent's knowledge.md knowledge base."""
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
    manager: AgentManager = Depends(_get_agent_manager),
    file_mgr: FileManager = Depends(_get_file_manager),
):
    """Upload files to an agent's workspace."""
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
    manager: AgentManager = Depends(_get_agent_manager),
    file_mgr: FileManager = Depends(_get_file_manager),
):
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
    manager: AgentManager = Depends(_get_agent_manager),
    file_mgr: FileManager = Depends(_get_file_manager),
):
    from fastapi.responses import Response

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


class AgentMessage(BaseModel):
    from_agent_id: str | None = None
    from_name: str | None = None
    text: str


@router.post("/{agent_id}/message")
async def send_message_to_agent(
    agent_id: str,
    body: AgentMessage,
    manager: AgentManager = Depends(_get_agent_manager),
    redis: RedisService = Depends(get_redis_service),
):
    """Send a message to an agent's chat queue (for inter-agent or external messaging)."""
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        # Build message with sender context
        sender = body.from_name or body.from_agent_id or "System"
        text = f"[Message from {sender}]: {body.text}"

        message_id = uuid.uuid4().hex[:12]
        chat_payload = json.dumps({
            "id": message_id,
            "text": text,
            "model": None,
        })

        await redis.client.lpush(f"agent:{agent_id}:chat", chat_payload)
        return {"status": "sent", "message_id": message_id, "to_agent": agent_id}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/{agent_id}/chat/sessions")
async def get_chat_sessions(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all chat sessions for an agent."""
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
    db: AsyncSession = Depends(get_db),
):
    """Get chat history for an agent, optionally filtered by session."""
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
                # Use DB auto-increment id to guarantee uniqueness
                # (message_id is a correlation ID shared between user & assistant)
                "id": str(msg.id),
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
    db: AsyncSession = Depends(get_db),
):
    """Delete all messages in a chat session."""
    from sqlalchemy import delete as sql_delete
    result = await db.execute(
        sql_delete(ChatMessage)
        .where(ChatMessage.agent_id == agent_id)
        .where(ChatMessage.session_id == session_id)
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.get("/{agent_id}/integrations")
async def get_agent_integrations(
    agent_id: str,
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get the list of enabled integrations for an agent."""
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
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Update the enabled integrations for an agent."""
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        config["integrations"] = body.get("integrations", [])
        agent.config = config
        # Force SQLAlchemy to detect the change on mutable JSON
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()
        return {"agent_id": agent_id, "integrations": config["integrations"]}
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
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get proactive mode config and schedule stats for an agent."""
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
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Enable, disable, or update proactive mode for an agent."""
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
            # Update existing schedule
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
                schedule_id = None  # Schedule was deleted, recreate

        if not schedule_id:
            # Create new schedule
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
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Disable and remove proactive mode for an agent."""
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


@router.get("/team/directory")
async def get_team_directory(
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Get the team directory - all agents with their roles and status."""
    agents = await manager.list_agents()
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
