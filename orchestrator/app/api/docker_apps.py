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

import httpx
import yaml
from docker.errors import ContainerError, ImageNotFound, NotFound
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
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


def _parse_port_target(entry: Any) -> str | None:
    """Extract the container-side (target) port from a compose ``ports`` entry.

    Handles "3000", "3001:3000", "127.0.0.1:3001:3000", "3000/tcp" and the long
    form {target: 3000, published: 3001}. Returns None if it can't be determined.
    """
    if isinstance(entry, dict):
        t = entry.get("target")
        return str(t) if t not in (None, "") else None
    s = str(entry).split("/", 1)[0]  # drop /tcp|/udp
    parts = [p for p in s.split(":")]
    last = parts[-1].strip() if parts else ""
    return last or None


def _prepare_free_port_compose(
    docker: DockerService, agent: Agent, path: str, compose_file: str
) -> str:
    """Rewrite fixed host-port bindings to Docker-auto-assigned free ports.

    True one-click deploy: a compose file with a hard-coded ``3001:3000`` fails the
    second time (``port is already allocated``). We generate a sidecar compose file
    (original untouched) where each service publishes ONLY the container port, so
    Docker picks a guaranteed-free host port. The actual assigned port is read back
    afterwards via ``_get_project_containers``. Falls back to the original file on
    any parse issue.
    """
    try:
        ec, content = docker.exec_in_container(agent.container_id, ["cat", compose_file])
        if ec != 0 or not content:
            return compose_file
        data = yaml.safe_load(content)
        if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
            return compose_file
        changed = False
        for svc in data["services"].values():
            if not isinstance(svc, dict) or not svc.get("ports"):
                continue
            new_ports = []
            for entry in svc["ports"]:
                target = _parse_port_target(entry)
                if target and target.isdigit():
                    new_ports.append(target)  # container port only → Docker auto-assigns host port
                    changed = True
                else:
                    new_ports.append(entry)
            svc["ports"] = new_ports
        if not changed:
            return compose_file
        # Write next to the original so relative build/env_file paths still resolve.
        gen_path = f"/workspace/{path}/docker-compose.aiemployee.yml"
        docker.write_file_in_container(
            agent.container_id, gen_path, yaml.safe_dump(data, sort_keys=False)
        )
        logger.info(f"Auto free-port compose generated for {path}: {gen_path}")
        return gen_path
    except Exception as e:  # noqa: BLE001 — never block deploy on this; use original
        logger.warning(f"free-port compose prep failed for {path}: {e}")
        return compose_file


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

    # Ensure referenced env files exist — compose hard-fails if an `env_file:` target
    # is missing. Touch the project-root `.env` plus every `env_file` declared in the
    # compose (also nested like `backend/.env`), creating parent dirs as needed.
    env_targets: set[str] = {".env"}
    try:
        _content = docker.get_file_from_container(agent.container_id, compose_file)
        _spec = yaml.safe_load(_content.decode("utf-8")) or {}
        for _svc in (_spec.get("services") or {}).values():
            if not isinstance(_svc, dict):
                continue
            _ef = _svc.get("env_file")
            for _e in ([_ef] if isinstance(_ef, str) else (_ef or [])):
                if isinstance(_e, str) and ".." not in _e and not _e.startswith("/"):
                    env_targets.add(_e)
    except Exception as _e:  # noqa: BLE001 — best-effort; fall back to just .env
        logger.debug("env_file scan failed for %s: %s", path, _e)
    for _rel in env_targets:
        full = f"/workspace/{path}/{_rel}"
        q = shlex.quote(full)
        # Robust: create the parent dir, remove an accidentally-created EMPTY dir at the
        # target (Docker creates missing bind/env sources as dirs → compose then fails
        # with "is a directory"), then create the file only if it doesn't exist.
        docker.exec_in_container(
            agent.container_id,
            f'f={q}; mkdir -p "$(dirname "$f")"; [ -d "$f" ] && rmdir "$f" 2>/dev/null; [ -e "$f" ] || touch "$f"',
        )

    logger.info(f"Starting Docker app: {project_name} (path={path}, agent={agent_id})")

    # One-click: rewrite fixed host ports to auto-assigned free ones so a re-deploy
    # never fails on "port is already allocated".
    compose_file = await asyncio.to_thread(
        _prepare_free_port_compose, docker, agent, path, compose_file
    )

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

    compose_file = await asyncio.to_thread(
        _prepare_free_port_compose, docker, agent, path, compose_file
    )

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


# ══════════════════════════════════════════════════════════════════════════════
# APP REVERSE-PROXY — reach a deployed app THROUGH the platform (Cloudflare+Caddy
# already forward /api/* here), instead of hostname:hostport which the tunnel does
# not expose. Auth + strict ownership so only the owner reaches their own app.
# ══════════════════════════════════════════════════════════════════════════════

# Hop-by-hop headers must not be forwarded by a proxy (RFC 7230 §6.1) + Host/length
# which httpx sets itself.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "content-encoding",
}


@router.api_route(
    "/proxy/{container}/{port}/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_app(
    agent_id: str,
    container: str,
    port: str,
    rest: str,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Reverse-proxy an HTTP request to one of THIS agent's deployed app containers.

    Reachable at ``/agents/{id}/apps/proxy/{container}/{port}/…`` — served through the
    same Cloudflare+Caddy chain as the rest of ``/api/*`` (no exposed host port needed).
    Two SSRF gates: (1) the container name must carry this agent's project prefix, and
    (2) its compose ``project`` label (set server-side via ``-p``) must match too — so a
    logged-in owner can only ever reach their OWN apps, never platform/other containers.
    """
    await _get_agent(agent_id, user, db)  # ownership + running-container check

    prefix = f"agent-{agent_id[:8]}-"
    if "/" in container or ".." in container or not container.startswith(prefix):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise HTTPException(status_code=400, detail="Invalid port")

    # Authoritative check: the container must belong to a compose project we started
    # for THIS agent (the project label is set by our own `-p`, not by the agent).
    try:
        c = docker.client.containers.get(container)
    except Exception:
        raise HTTPException(status_code=404, detail="App container not found")
    if not str(c.labels.get("com.docker.compose.project", "")).startswith(prefix):
        raise HTTPException(status_code=403, detail="Forbidden")

    target = f"http://{container}:{int(port)}/{rest}"
    body = await request.body()
    # NEVER forward the platform auth credentials to the app — it runs agent-authored
    # code and could otherwise read the owner's session cookie / bearer token.
    _strip = _HOP_BY_HOP | {"cookie", "authorization"}
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _strip
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(
                request.method, target,
                params=dict(request.query_params),
                headers=fwd_headers, content=body,
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"App nicht erreichbar: {type(e).__name__}")

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "content-security-policy"
    }
    # The app is agent-authored code served from the PLATFORM origin. Without this it
    # would run same-origin and could call the platform API with the owner's ambient
    # cookie. Force a CSP sandbox → the document gets an opaque origin (no access to
    # platform cookies/localStorage/API), while its own scripts/forms still run.
    resp_headers["content-security-policy"] = "sandbox allow-scripts allow-forms allow-popups allow-modals;"
    resp_headers["x-content-type-options"] = "nosniff"
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
