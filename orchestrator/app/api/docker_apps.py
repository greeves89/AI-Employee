"""Docker Apps API - Start/stop docker-compose projects from agent workspaces.

Agents develop projects in their /workspace directory. If a project has a
docker-compose.yml, users can start/stop/monitor the app from the Web UI.

How it works:
1. Discovery: exec into agent container to find docker-compose.yml files
2. Start/Stop: run a docker:cli container with workspace volume + docker socket
3. Status: query Docker API for containers with the project label
4. Logs: stream container logs via Docker API
"""

import asyncio
import logging
import re
import shlex
from typing import Any

import yaml
from docker.errors import ContainerError, ImageNotFound, NotFound
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_docker_service, require_auth
from app.models.agent import Agent
from app.services.docker_service import DockerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/apps", tags=["docker-apps"])

COMPOSE_RUNNER_IMAGE = "docker:cli"


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════


async def _get_agent(agent_id: str, user, db: AsyncSession) -> Agent:
    """Get agent and verify ownership."""
    from app.dependencies import require_agent_access
    await require_agent_access(agent_id, user, db)

    from sqlalchemy import select
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.container_id:
        raise HTTPException(status_code=400, detail="Agent has no running container")
    return agent


def _project_name(agent_id: str, app_path: str) -> str:
    """Generate a unique docker compose project name."""
    safe = re.sub(r"[^a-z0-9-]", "-", app_path.lower().strip("/"))
    return f"agent-{agent_id[:8]}-{safe}"


def _run_compose(
    docker: DockerService,
    workspace_volume: str,
    project_name: str,
    compose_file: str,
    command: list[str],
    network: str = "ai-employee-network",
) -> tuple[int, str]:
    """Run a docker compose command in a runner container.

    Uses docker:cli image with the workspace volume and Docker socket mounted
    so compose has access to both the project files and the Docker daemon.
    """
    full_command = [
        "docker", "compose",
        "-p", project_name,
        "-f", compose_file,
    ] + command

    try:
        # Use stderr=True to merge stderr into output (warnings + errors visible)
        output = docker.client.containers.run(
            image=COMPOSE_RUNNER_IMAGE,
            command=full_command,
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                workspace_volume: {"bind": "/workspace", "mode": "rw"},
            },
            network=network,
            remove=True,
            detach=False,
            stderr=True,
        )
        text = output.decode("utf-8") if isinstance(output, bytes) else str(output)
        return 0, text
    except ContainerError as e:
        # Collect both stdout and stderr for diagnostics
        parts = []
        if e.stderr:
            parts.append(e.stderr.decode("utf-8") if isinstance(e.stderr, bytes) else str(e.stderr))
        if hasattr(e, "output") and e.output:
            parts.append(e.output.decode("utf-8") if isinstance(e.output, bytes) else str(e.output))
        combined = "\n".join(parts).strip()
        return e.exit_status or 1, combined
    except ImageNotFound:
        raise HTTPException(
            status_code=503,
            detail=f"Docker image '{COMPOSE_RUNNER_IMAGE}' not found. "
            "Pull it with: docker pull docker:cli",
        )


def _get_project_containers(docker: DockerService, project_name: str) -> list[dict]:
    """List all containers belonging to a compose project."""
    containers = docker.client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.project={project_name}"},
    )
    results = []
    for c in containers:
        # Extract port mappings (published ports)
        ports = []
        for port_key, bindings in (c.ports or {}).items():
            if bindings:
                for b in bindings:
                    ports.append({
                        "host_port": int(b["HostPort"]),
                        "container_port": port_key,
                        "host_ip": b.get("HostIp", "0.0.0.0"),
                    })

        # Also detect exposed but unmapped ports from the image
        exposed_ports = []
        config_ports = c.attrs.get("Config", {}).get("ExposedPorts", {})
        mapped_container_ports = {p["container_port"] for p in ports}
        for port_key in (config_ports or {}):
            if port_key not in mapped_container_ports:
                exposed_ports.append(port_key)

        try:
            image_name = c.image.tags[0] if c.image.tags else str(c.image.id)[:20]
        except Exception:
            image_name = c.attrs.get("Config", {}).get("Image", "unknown")

        results.append({
            "id": c.short_id,
            "name": c.name,
            "service": c.labels.get("com.docker.compose.service", "unknown"),
            "image": image_name,
            "status": c.status,
            "state": c.attrs.get("State", {}).get("Status", "unknown"),
            "ports": ports,
            "exposed_ports": exposed_ports,
        })
    return results


AGENT_NETWORK = "ai-employee-network"


def _connect_containers_to_network(docker: DockerService, project_name: str) -> None:
    """Connect all containers of a compose project to the ai-employee-network."""
    try:
        network = docker.client.networks.get(AGENT_NETWORK)
    except Exception:
        logger.warning(f"Network {AGENT_NETWORK} not found, skipping")
        return

    containers = docker.client.containers.list(
        filters={"label": f"com.docker.compose.project={project_name}"},
    )
    for c in containers:
        # Check if already connected
        connected_nets = c.attrs.get("NetworkSettings", {}).get("Networks", {})
        if AGENT_NETWORK not in connected_nets:
            try:
                network.connect(c)
                logger.info(f"Connected {c.name} to {AGENT_NETWORK}")
            except Exception as e:
                logger.warning(f"Failed to connect {c.name} to {AGENT_NETWORK}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


@router.get("")
async def discover_apps(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Discover docker-compose.yml files in agent workspace.

    Scans the workspace for docker-compose.yml and docker-compose.yaml files,
    parses them, and returns a list of available apps with their services.
    """
    agent = await _get_agent(agent_id, user, db)

    # Find all compose files in workspace (max depth 3 to avoid deep recursion)
    exit_code, stdout = docker.exec_in_container(
        agent.container_id,
        "find /workspace -maxdepth 3 -name 'docker-compose.yml' -o -name 'docker-compose.yaml' -o -name 'compose.yml' -o -name 'compose.yaml'",
    )

    if exit_code != 0 or not stdout.strip():
        return {"apps": []}

    apps = []
    for compose_path in stdout.strip().split("\n"):
        compose_path = compose_path.strip()
        if not compose_path:
            continue

        # Get the project directory (relative to /workspace)
        project_dir = "/".join(compose_path.split("/")[:-1])
        rel_path = project_dir.replace("/workspace/", "").replace("/workspace", "")
        if not rel_path:
            rel_path = "."
        app_name = rel_path.split("/")[-1] if rel_path != "." else "root"
        compose_filename = compose_path.split("/")[-1]

        # Parse compose file
        try:
            content = docker.get_file_from_container(agent.container_id, compose_path)
            parsed = yaml.safe_load(content.decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to parse {compose_path}: {e}")
            apps.append({
                "name": app_name,
                "path": rel_path,
                "compose_file": compose_filename,
                "services": [],
                "error": f"Failed to parse: {e}",
            })
            continue

        if not parsed or not isinstance(parsed, dict):
            continue

        # Extract services
        services_def = parsed.get("services", {})
        services = []
        for svc_name, svc_config in services_def.items():
            svc_info: dict[str, Any] = {
                "name": svc_name,
                "image": svc_config.get("image", ""),
                "build": bool(svc_config.get("build")),
                "ports": svc_config.get("ports", []),
            }
            services.append(svc_info)

        # Check if this project is currently running
        project_name = _project_name(agent_id, rel_path)
        running_containers = _get_project_containers(docker, project_name)
        status = "stopped"
        if running_containers:
            running_count = sum(1 for c in running_containers if c["status"] == "running")
            if running_count == len(running_containers):
                status = "running"
            elif running_count > 0:
                status = "partial"
            else:
                status = "stopped"

        apps.append({
            "name": app_name,
            "path": rel_path,
            "compose_file": compose_filename,
            "services": services,
            "status": status,
            "containers": running_containers,
        })

    return {"apps": apps}


@router.post("/up")
async def start_app(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Start a docker-compose project from agent workspace."""
    agent = await _get_agent(agent_id, user, db)

    # Validate path to prevent directory traversal
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    # Verify compose file exists (shell-quote path to prevent injection)
    safe_path = shlex.quote(f"/workspace/{path}/docker-compose.yml")
    exit_code, _ = docker.exec_in_container(
        agent.container_id, f"test -f {safe_path}"
    )
    compose_file = f"/workspace/{path}/docker-compose.yml"
    if exit_code != 0:
        # Try alternative names
        for alt in ["docker-compose.yaml", "compose.yml", "compose.yaml"]:
            alt_path = f"/workspace/{path}/{alt}"
            safe_alt = shlex.quote(alt_path)
            ec, _ = docker.exec_in_container(agent.container_id, f"test -f {safe_alt}")
            if ec == 0:
                compose_file = alt_path
                break
        else:
            raise HTTPException(
                status_code=404,
                detail=f"No docker-compose file found in /workspace/{path}/",
            )

    project_name = _project_name(agent_id, path)
    workspace_volume = agent.volume_name or f"workspace-{agent_id}"

    # Ensure .env file exists (compose fails if env_file references missing .env)
    docker.exec_in_container(
        agent.container_id,
        f"touch {shlex.quote(f'/workspace/{path}/.env')}",
    )

    logger.info(f"Starting Docker app: {project_name} (path={path}, agent={agent_id})")

    # Run compose up in background to not block the request
    exit_code, output = await asyncio.to_thread(
        _run_compose,
        docker, workspace_volume, project_name, compose_file,
        ["up", "-d", "--build"],
    )

    if exit_code != 0:
        logger.error(f"Failed to start {project_name}: {output}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start app: {output}",
        )

    # Connect app containers to agent network so agent can reach them
    _connect_containers_to_network(docker, project_name)

    # Get status of started containers
    containers = _get_project_containers(docker, project_name)

    logger.info(f"Docker app started: {project_name} ({len(containers)} containers)")

    return {
        "project": project_name,
        "status": "running",
        "containers": containers,
        "output": output,
    }


@router.post("/down")
async def stop_app(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Stop a docker-compose project."""
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    agent = await _get_agent(agent_id, user, db)

    project_name = _project_name(agent_id, path)
    workspace_volume = agent.volume_name or f"workspace-{agent_id}"

    # Find compose file
    compose_file = f"/workspace/{path}/docker-compose.yml"
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        candidate = f"/workspace/{path}/{name}"
        ec, _ = docker.exec_in_container(agent.container_id, f"test -f {shlex.quote(candidate)}")
        if ec == 0:
            compose_file = candidate
            break

    logger.info(f"Stopping Docker app: {project_name}")

    exit_code, output = await asyncio.to_thread(
        _run_compose,
        docker, workspace_volume, project_name, compose_file,
        ["down"],
    )

    if exit_code != 0:
        logger.warning(f"Compose down warning for {project_name}: {output}")

    return {
        "project": project_name,
        "status": "stopped",
        "output": output,
    }


@router.get("/status")
async def app_status(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Get status of a running docker-compose project."""
    await _get_agent(agent_id, user, db)

    project_name = _project_name(agent_id, path)
    containers = _get_project_containers(docker, project_name)

    running_count = sum(1 for c in containers if c["status"] == "running")
    total = len(containers)

    if total == 0:
        status = "stopped"
    elif running_count == total:
        status = "running"
    elif running_count > 0:
        status = "partial"
    else:
        status = "stopped"

    return {
        "project": project_name,
        "status": status,
        "containers": containers,
        "running": running_count,
        "total": total,
    }


@router.get("/logs")
async def app_logs(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    service: str | None = Query(None, description="Specific service name"),
    lines: int = Query(100, ge=10, le=1000),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Get logs from a docker-compose project's containers."""
    await _get_agent(agent_id, user, db)

    project_name = _project_name(agent_id, path)
    containers = _get_project_containers(docker, project_name)

    if not containers:
        return {"logs": [], "project": project_name}

    # Filter by service if specified
    if service:
        containers = [c for c in containers if c["service"] == service]
        if not containers:
            raise HTTPException(status_code=404, detail=f"Service '{service}' not found")

    logs = []
    for container_info in containers:
        try:
            container = docker.client.containers.get(container_info["id"])
            log_output = container.logs(tail=lines, timestamps=True).decode("utf-8")
            for line in log_output.strip().split("\n"):
                if line:
                    logs.append({
                        "service": container_info["service"],
                        "line": line,
                    })
        except Exception as e:
            logs.append({
                "service": container_info["service"],
                "line": f"[Error reading logs: {e}]",
            })

    return {
        "logs": logs,
        "project": project_name,
        "total_lines": len(logs),
    }


@router.post("/rebuild")
async def rebuild_app(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Rebuild and restart a docker-compose project (forces image rebuild)."""
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    agent = await _get_agent(agent_id, user, db)

    compose_file = f"/workspace/{path}/docker-compose.yml"
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        candidate = f"/workspace/{path}/{name}"
        ec, _ = docker.exec_in_container(agent.container_id, f"test -f {shlex.quote(candidate)}")
        if ec == 0:
            compose_file = candidate
            break

    project_name = _project_name(agent_id, path)
    workspace_volume = agent.volume_name or f"workspace-{agent_id}"

    # Ensure .env file exists
    docker.exec_in_container(agent.container_id, f"touch {shlex.quote(f'/workspace/{path}/.env')}")

    logger.info(f"Rebuilding Docker app: {project_name}")

    exit_code, output = await asyncio.to_thread(
        _run_compose,
        docker, workspace_volume, project_name, compose_file,
        ["up", "-d", "--build", "--force-recreate"],
    )

    if exit_code != 0:
        logger.error(f"Failed to rebuild {project_name}: {output}")
        raise HTTPException(status_code=500, detail=f"Failed to rebuild app: {output}")

    # Re-connect to agent network (force-recreate drops network connections)
    _connect_containers_to_network(docker, project_name)

    containers = _get_project_containers(docker, project_name)
    return {
        "project": project_name,
        "status": "running",
        "containers": containers,
        "output": output,
    }


@router.post("/restart-service")
async def restart_service(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    service: str = Query(..., description="Service name to restart"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Restart a single service container."""
    await _get_agent(agent_id, user, db)

    project_name = _project_name(agent_id, path)
    containers = _get_project_containers(docker, project_name)

    target = [c for c in containers if c["service"] == service]
    if not target:
        raise HTTPException(status_code=404, detail=f"Service '{service}' not found")

    for c in target:
        try:
            container = docker.client.containers.get(c["id"])
            container.restart(timeout=10)
            logger.info(f"Restarted service {service} in {project_name}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to restart {service}: {e}")

    containers = _get_project_containers(docker, project_name)
    return {
        "project": project_name,
        "service": service,
        "status": "restarted",
        "containers": containers,
    }
