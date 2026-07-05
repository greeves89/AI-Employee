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
    """List ALL apps the caller's agents have — running, stopped AND never-started
    compose projects in the agent workspaces (admin: all agents)."""
    from app.api.docker_apps import _project_name

    owned = await _visible_agents(user, db)
    if not owned:
        return {"apps": []}
    id_to_agent = {a.id: a for a in owned.values()}
    apps: dict[str, dict] = {}

    # 1. Never-/previously-started compose projects discovered in each agent workspace.
    #    (Requires a running agent container to exec `find`; idle agents are skipped —
    #    their already-started apps still show via the container scan below.)
    for agent in owned.values():
        if not agent.container_id:
            continue
        try:
            ec, out = docker.exec_in_container(
                agent.container_id,
                "find /workspace -maxdepth 3 \\( -name docker-compose.yml -o -name docker-compose.yaml -o -name compose.yml -o -name compose.yaml \\)",
            )
        except Exception:  # noqa: BLE001
            continue
        if ec != 0 or not (out or "").strip():
            continue
        for compose_path in out.strip().splitlines():
            compose_path = compose_path.strip()
            if not compose_path:
                continue
            # Skip the AI-Employee platform repo itself (agents often have it cloned in
            # their workspace). It is NOT a user app — it's the platform that already
            # runs — and building it inside an agent is nonsensical + huge. Detect it by
            # its unique infra markers.
            try:
                _txt = docker.get_file_from_container(agent.container_id, compose_path).decode("utf-8", "replace")
                if any(m in _txt for m in ("docker-socket-proxy", "ai-employee-orchestrator", "ai-employee-shared", "ai-employee-network")):
                    continue
            except Exception:  # noqa: BLE001
                pass
            project_dir = "/".join(compose_path.split("/")[:-1])
            rel_path = project_dir.replace("/workspace/", "").replace("/workspace", "") or "."
            name = rel_path.split("/")[-1] if rel_path != "." else "root"
            proj = _project_name(agent.id, rel_path)
            apps.setdefault(proj, {
                "project": proj, "agent_id": agent.id, "agent_name": agent.name,
                "name": name, "path": rel_path, "status": "not_started",
                "containers": [], "url": None,
            })

    # 2. Actual containers (running/stopped) — taskforce apps + anything started.
    try:
        containers = docker.client.containers.list(
            all=True, filters={"label": "com.docker.compose.project"}
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[Apps] container list failed: %s", e)
        containers = []
    for c in containers:
        proj = str(c.labels.get("com.docker.compose.project", ""))
        agent = next((a for pre, a in owned.items() if proj.startswith(pre)), None)
        if not agent:
            continue
        entry = apps.setdefault(proj, {
            "project": proj, "agent_id": agent.id, "agent_name": agent.name,
            "name": proj, "path": None, "status": "stopped",
            "containers": [], "url": None,
        })
        entry["containers"].append({
            "name": c.name, "status": c.status,
            "service": c.labels.get("com.docker.compose.service", ""),
        })
        if c.status == "running":
            entry["status"] = "running"
            port = _first_port(c)
            if not entry["url"] and port:
                entry["url"] = f"/api/v1/agents/{agent.id}/apps/proxy/{c.name}/{port}/"
        elif entry["status"] == "not_started":
            entry["status"] = "stopped"  # has containers but none running

    return {"apps": sorted(apps.values(), key=lambda a: (a["agent_name"], a["name"]))}


async def _project_containers_owned(project: str, user, db: AsyncSession, docker: DockerService):
    """Return the app's containers IFF the project belongs to one of the caller's
    agents. Raises 404 otherwise (ownership gate on the project prefix)."""
    owned = await _visible_agents(user, db)
    if not any(project.startswith(pre) for pre in owned):
        raise HTTPException(status_code=404, detail="App not found")
    try:
        return docker.client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={project}"}
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Fehler beim Zugriff auf die App.")


@router.post("/stop")
async def stop_app(
    project: str = Query(..., description="Compose project name to stop"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """STOP (not remove) all containers → the app stays visible as 'gestoppt' and can
    be started again. Ownership-gated."""
    containers = await _project_containers_owned(project, user, db, docker)
    n = 0
    for c in containers:
        try:
            c.stop(timeout=8)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return {"project": project, "stopped": n}


@router.post("/start")
async def start_app(
    project: str = Query(..., description="Compose project name to start"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """(Re)start a previously stopped app's containers. Ownership-gated."""
    containers = await _project_containers_owned(project, user, db, docker)
    if not containers:
        raise HTTPException(status_code=404, detail="Keine Container mehr — App neu aus dem Meeting starten.")
    n = 0
    for c in containers:
        try:
            c.start()
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return {"project": project, "started": n}


@router.post("/remove")
async def remove_app(
    project: str = Query(..., description="Compose project name to remove"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Permanently remove an app's containers (endgültig). Ownership-gated."""
    containers = await _project_containers_owned(project, user, db, docker)
    n = 0
    for c in containers:
        try:
            c.remove(force=True)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return {"project": project, "removed": n}


@router.get("/logs")
async def app_logs(
    project: str = Query(..., description="Compose project name"),
    tail: int = Query(200, ge=1, le=2000),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Return the last N log lines per container of an app (for the log/diagnostics
    view). Ownership-gated."""
    containers = await _project_containers_owned(project, user, db, docker)
    out = []
    for c in containers:
        try:
            raw = c.logs(tail=tail, timestamps=False)
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:  # noqa: BLE001
            text = "(Logs nicht verfügbar)"
        out.append({
            "name": c.name,
            "service": c.labels.get("com.docker.compose.service", ""),
            "status": c.status,
            "logs": text,
        })
    return {"project": project, "containers": out}
