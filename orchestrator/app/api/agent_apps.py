"""Agent-facing Docker Apps API.

Same operations as ``docker_apps`` (discover/start/stop/rebuild/logs of the
docker-compose projects in the agent's workspace) but authenticated by the AGENT
itself via ``verify_agent_token`` and HARD-SCOPED to the caller's own agent_id —
an agent can only ever touch ITS OWN apps.

The agent container has no Docker; it drives these operations through the
orchestrator (which owns the docker socket) via its MCP ``orchestrator-server``
tools (list_apps/app_logs/start_app/stop_app/rebuild_app). This closes the loop:
the agent edits an app's code in its workspace, then rebuilds it itself.

All real logic lives in ``docker_apps`` (the *_core helpers) — this module is a
thin, self-scoped auth wrapper. Never duplicate app logic here.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.docker_apps import (
    _discover_core,
    _load_agent,
    _logs_core,
    _rebuild_core,
    _start_core,
    _stop_core,
)
from app.core.agent_manager import AgentManager
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, verify_agent_token
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-apps", tags=["agent-apps"])


@router.get("")
async def agent_list_apps(
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """List the agent's own apps (docker-compose projects) + running status.

    Anders als die Verwaltungsoberflaeche bekommt der Agent auch Projekte
    gemeldet, die nur ein Dockerfile haben: Sie sind nicht startbar, aber der
    Agent muss sie sehen, um zu erkennen, dass ihm die compose-Datei fehlt.
    """
    agent_id = auth["agent_id"]
    agent = await _load_agent(agent_id, db)
    return await _discover_core(docker, agent, agent_id, include_dockerfile_only=True)


@router.get("/logs")
async def agent_app_logs(
    path: str = Query(..., description="Relative path to the app in /workspace"),
    service: str | None = Query(None, description="Specific service name"),
    lines: int = Query(100, ge=10, le=1000),
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Return container logs for one of the agent's own apps."""
    import asyncio
    agent_id = auth["agent_id"]
    await _load_agent(agent_id, db)  # existence/ownership gate
    return await asyncio.to_thread(_logs_core, docker, agent_id, path, service, lines)


@router.post("/up")
async def agent_start_app(
    path: str = Query(..., description="Relative path to the app in /workspace"),
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Start one of the agent's own apps (docker compose up -d --build)."""
    agent_id = auth["agent_id"]
    agent = await _load_agent(agent_id, db)
    return await _start_core(docker, agent, agent_id, path)


@router.post("/down")
async def agent_stop_app(
    path: str = Query(..., description="Relative path to the app in /workspace"),
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Stop one of the agent's own apps (docker compose down)."""
    agent_id = auth["agent_id"]
    agent = await _load_agent(agent_id, db)
    return await _stop_core(docker, agent, agent_id, path)


@router.post("/rebuild")
async def agent_rebuild_app(
    path: str = Query(..., description="Relative path to the app in /workspace"),
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Rebuild one of the agent's own apps from its CURRENT code
    (docker compose up -d --build --force-recreate) — so code/config changes the
    agent just made in the workspace actually take effect."""
    agent_id = auth["agent_id"]
    agent = await _load_agent(agent_id, db)
    return await _rebuild_core(docker, agent, agent_id, path)


@router.post("/restart-self")
async def agent_restart_self(
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Rebuild the calling agent's OWN container from its current image/config,
    preserving its workspace volume — the chat-triggered equivalent of the
    admin "Update available" button. Unlike ``agents.py``'s owner-gated
    ``/{agent_id}/update``, this is scoped by the agent's own token
    (``verify_agent_token``), not a human JWT — an agent has no user session
    to authenticate a call about itself with.

    Interrupts whatever the agent is currently doing. The tool description on
    the caller side tells the agent to announce this before calling, not do
    it silently — same convention as other destructive tools."""
    agent_id = auth["agent_id"]
    agent = await _load_agent(agent_id, db)

    from app.services.eval_service import gate_for_agent
    decision = await gate_for_agent(db, agent)
    if not decision.get("allowed"):
        raise HTTPException(
            status_code=409,
            detail={"error": "eval_gate", "message": decision.get("message"),
                    **{k: v for k, v in decision.items() if k != "message"}},
        )

    manager = AgentManager(db, docker, redis)
    try:
        await manager.update_agent(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "restarted", "agent_id": agent_id}
