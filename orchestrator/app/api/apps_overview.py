"""Global Apps overview — lists the running Docker apps a user is allowed to see.

Ownership (CRITICAL): a user only ever sees apps that belong to THEIR OWN agents.
App compose projects are named ``agent-{agentId8}-…`` (see docker_apps._project_name),
so we map each running app project back to an agent and keep only the ones the caller
owns. Admins see all. This is the platform-wide counterpart to the per-agent
``/agents/{id}/apps`` discovery — same ownership model, one screen.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_docker_service, require_auth
from app.models.agent import Agent
from app.models.user import UserRole
from app.services.docker_service import DockerService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apps", tags=["apps"])


async def _visible_agents(user, db: AsyncSession) -> dict[str, Agent]:
    """Map ``agent-{id8}-`` project prefix → Agent, for every agent the user may see."""
    if getattr(user, "role", None) == UserRole.ADMIN:
        rows = (await db.execute(select(Agent))).scalars().all()
    else:
        rows = (await db.execute(
            select(Agent).where(Agent.user_id == str(getattr(user, "id", "")))
        )).scalars().all()
    return {f"agent-{a.id[:8]}-": a for a in rows}


def _first_port(container) -> str | None:
    for pk in (container.attrs.get("Config", {}).get("ExposedPorts") or {}):
        p = str(pk).split("/")[0]
        if p.isdigit():
            return p
    # Fall back to published ports
    for pk in (container.attrs.get("NetworkSettings", {}).get("Ports") or {}):
        p = str(pk).split("/")[0]
        if p.isdigit():
            return p
    return None


@router.get("")
async def list_apps(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """List the Docker apps that belong to the caller's agents (admin: all)."""
    owned = await _visible_agents(user, db)
    if not owned:
        return {"apps": []}
    try:
        containers = docker.client.containers.list(
            all=True, filters={"label": "com.docker.compose.project"}
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[Apps] list failed: %s", e)
        return {"apps": []}

    apps: dict[str, dict] = {}
    for c in containers:
        proj = str(c.labels.get("com.docker.compose.project", ""))
        agent = next((a for pre, a in owned.items() if proj.startswith(pre)), None)
        if not agent:
            continue  # not one of the caller's apps → invisible
        entry = apps.setdefault(proj, {
            "project": proj,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "status": "stopped",
            "containers": [],
            "url": None,
        })
        entry["containers"].append({
            "name": c.name,
            "status": c.status,
            "service": c.labels.get("com.docker.compose.service", ""),
        })
        if c.status == "running":
            entry["status"] = "running"
            port = _first_port(c)
            if not entry["url"] and port:
                entry["url"] = f"/api/v1/agents/{agent.id}/apps/proxy/{c.name}/{port}/"
    return {"apps": sorted(apps.values(), key=lambda a: (a["agent_name"], a["project"]))}


@router.post("/stop")
async def stop_app(
    project: str = Query(..., description="Compose project name to stop"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Stop + remove all containers of an app project — only if it belongs to one of
    the caller's agents (ownership gate on the project prefix)."""
    owned = await _visible_agents(user, db)
    if not any(project.startswith(pre) for pre in owned):
        raise HTTPException(status_code=404, detail="App not found")
    try:
        containers = docker.client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={project}"}
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Stop fehlgeschlagen.")
    removed = 0
    for c in containers:
        try:
            c.remove(force=True)
            removed += 1
        except Exception:  # noqa: BLE001
            pass
    return {"project": project, "removed": removed}
