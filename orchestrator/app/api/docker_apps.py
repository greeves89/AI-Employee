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
from urllib.parse import urlencode

import httpx
import yaml
from docker.errors import APIError, ContainerError, ImageNotFound, NotFound
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_sharing import (
    ACCESS_OWNER,
    ACCESS_PUBLIC,
    agent_has_active_shares,
    is_app_owner,
    resolve_app_access,
)
from app.core.log_redaction import redact_logs, scrub_log
from app.db.session import get_db
from app.dependencies import get_docker_service, optional_auth, require_auth
from app.models.agent import Agent
from app.services.docker_service import DockerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/{agent_id}/apps", tags=["docker-apps"])

COMPOSE_RUNNER_IMAGE = "docker:cli"


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════


async def _load_agent(agent_id: str, db: AsyncSession) -> Agent:
    """Load an agent + verify it has a container. NO ownership check — the caller
    must have already established the principal (a user via _get_agent, or the agent
    itself via a verified agent token that IS this agent_id)."""
    from sqlalchemy import select
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.container_id:
        raise HTTPException(status_code=400, detail="Agent has no running container")
    return agent


async def _get_agent(agent_id: str, user, db: AsyncSession) -> Agent:
    """Get agent and verify the USER owns/has access to it."""
    from app.dependencies import require_agent_access
    await require_agent_access(agent_id, user, db)
    return await _load_agent(agent_id, db)


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


# Compose benennt den laufenden Container vor dem Ersetzen in ``<hex>_<name>`` um
# und legt den neuen unter dem echten Namen an. Bricht das dazwischen ab, bleibt
# die Sicherungskopie liegen — mitsamt den Projektbezeichnungen.
_BACKUP_NAME = re.compile(r"^/?[0-9a-f]{6,}_.+$")

_NAME_CONFLICT = re.compile(
    r'container name "?/?[^"\s]+"? is already in use', re.IGNORECASE
)

# Eine Sperre je App. Zwei Rebuilds derselben App gleichzeitig sind genau das,
# was die liegengebliebenen Kopien ueberhaupt erst erzeugt (#644).
_PROJECT_LOCKS: dict[str, asyncio.Lock] = {}


def _project_lock(project_name: str) -> asyncio.Lock:
    lock = _PROJECT_LOCKS.get(project_name)
    if lock is None:
        lock = _PROJECT_LOCKS[project_name] = asyncio.Lock()
    return lock


def _is_name_conflict(output: str | None) -> bool:
    """Nur ein Namenskonflikt rechtfertigt einen zweiten Anlauf. Ein voller
    Datentraeger oder ein kaputtes Dockerfile wird dadurch nicht besser — der
    Nutzer wartet dann bloss doppelt so lang auf dieselbe Fehlermeldung."""
    return bool(_NAME_CONFLICT.search(output or ""))


def _reconcile_stale_backups(docker: DockerService, project_name: str) -> list[str]:
    """Raeumt Recreate-Reste weg, die jeden weiteren Rebuild blockieren (#644).

    Entfernt wird nur, wo fuer denselben Platz (Dienst + Nummer) MEHRERE
    Container existieren — der Zustand, den ein abgebrochener Recreate
    hinterlaesst. Gibt es fuer einen Platz nur einen Container, ist er die App,
    auch wenn sein Name zufaellig nach Sicherungskopie aussieht.
    """
    try:
        containers = docker.client.containers.list(
            all=True,
            filters={"label": f"com.docker.compose.project={project_name}"},
        )
    except APIError as e:
        logger.warning(f"Could not list containers of {scrub_log(project_name)}: {scrub_log(str(e))}")
        return []

    plaetze: dict[tuple[str, str], list] = {}
    for c in containers:
        labels = c.labels or {}
        plaetze.setdefault(
            (
                labels.get("com.docker.compose.service", ""),
                labels.get("com.docker.compose.container-number", ""),
            ),
            [],
        ).append(c)

    entfernt: list[str] = []
    for gruppe in plaetze.values():
        if len(gruppe) < 2:
            continue
        for c in gruppe:
            name = c.name or ""
            if not _BACKUP_NAME.match(name):
                continue
            try:
                c.remove(force=True)
            except NotFound:
                continue        # ein paralleler Aufraeumer war schneller
            except APIError as e:
                # "removal of container is already in progress" — laeuft bereits
                logger.warning(f"Could not remove leftover {scrub_log(name)}: {scrub_log(str(e))}")
                continue
            entfernt.append(name)

    if entfernt:
        logger.info(
            f"Removed {len(entfernt)} leftover compose container(s) of "
            f"{scrub_log(project_name)}: {scrub_log(', '.join(entfernt))}"
        )
    return entfernt


async def _compose_with_conflict_recovery(
    docker: DockerService,
    workspace_volume: str,
    project_name: str,
    compose_file: str,
    command: list[str],
) -> tuple[int, str]:
    """Ein Compose-Lauf, der einen Namenskonflikt einmal selbst aufloest."""
    exit_code, output = await asyncio.to_thread(
        _run_compose, docker, workspace_volume, project_name, compose_file, command,
    )
    if exit_code == 0 or not _is_name_conflict(output):
        return exit_code, output

    logger.warning(
        f"Name conflict for {scrub_log(project_name)}, reconciling leftovers and retrying once"
    )
    await asyncio.to_thread(_reconcile_stale_backups, docker, project_name)
    return await asyncio.to_thread(
        _run_compose, docker, workspace_volume, project_name, compose_file, command,
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


# ══════════════════════════════════════════════════════════════════════════════
# CORE OPERATIONS (principal-agnostic — caller loads `agent` after authN/authZ)
# These are shared by the USER-facing endpoints (require_auth) and the AGENT-facing
# endpoints (verify_agent_token, self-scoped) in agent_apps.py. Keep app logic here
# only — never duplicate it in a router.
# ══════════════════════════════════════════════════════════════════════════════


def _app_public_url(agent_id: str, containers: list[dict]) -> str | None:
    """Build the absolute, tunnel-reachable app-proxy URL for a RUNNING app, or None.

    Needs ``settings.public_app_url`` (e.g. https://agents.future-app.de) plus a running
    container's NAME + its internal port. This is THE link an agent hands to the user
    (never localhost/host-port — those don't work off-device). Requires the platform
    login, like every other /api/* route.
    """
    from app.config import settings
    base = (settings.public_app_url or "").rstrip("/")
    if not base:
        return None
    for c in containers:
        if c.get("state") != "running" and c.get("status") != "running":
            continue
        name = c.get("name") or ""
        internal = ""
        for p in (c.get("ports") or []):
            cp = str(p.get("container_port") or "").split("/")[0]
            if cp.isdigit():
                internal = cp
                break
        if not internal:
            for ep in (c.get("exposed_ports") or []):
                cp = str(ep).split("/")[0]
                if cp.isdigit():
                    internal = cp
                    break
        if name and internal:
            return f"{base}/api/v1/agents/{agent_id}/apps/proxy/{name}/{internal}/"
    return None


async def _discover_core(
    docker: DockerService,
    agent: Agent,
    agent_id: str,
    *,
    include_dockerfile_only: bool = False,
) -> dict:
    """Apps im Workspace des Agenten finden.

    ``include_dockerfile_only`` meldet zusaetzlich Verzeichnisse, die zwar ein
    Dockerfile haben, aber keine compose-Datei. Sie sind nicht startbar — die
    Plattform fuehrt Anwendungen ausschliesslich ueber compose —, tauchten aber
    bisher nirgends auf. Fuer den Agenten sah sein eigenes Projekt damit aus wie
    nicht vorhanden, also griff er zu ``docker build`` und lief in ein
    fehlendes Docker-Kommando. Sichtbar mit Hinweis ist besser als unsichtbar.
    Die Verwaltungsoberflaeche bekommt sie nicht: dort waeren sie Eintraege,
    die niemand starten kann.
    """
    # Find all compose files in workspace (max depth 3 to avoid deep recursion).
    # The agent's own container may be momentarily stopped (DB still has its id) — in
    # that case exec raises a 409; treat it as "no reachable apps" instead of a 500.
    try:
        exit_code, stdout = docker.exec_in_container(
            agent.container_id,
            "find /workspace -maxdepth 3 -name 'docker-compose.yml' -o -name 'docker-compose.yaml' -o -name 'compose.yml' -o -name 'compose.yaml'",
        )
    except Exception as e:  # noqa: BLE001 — container not running / unreachable
        logger.info("discover_apps: workspace not reachable for agent %s: %s", scrub_log(agent_id), e)
        return {"apps": []}

    if exit_code != 0 or not stdout.strip():
        # Keine compose-Datei heisst nicht "keine Projekte": Genau hier liegt der
        # Fall, um den es geht — ein Verzeichnis mit blossem Dockerfile.
        return {"apps": _dockerfile_only_apps(docker, agent, agent_id, [])
                if include_dockerfile_only else []}

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
            "url": _app_public_url(agent_id, running_containers) if status in ("running", "partial") else None,
        })

    if include_dockerfile_only:
        apps.extend(_dockerfile_only_apps(docker, agent, agent_id, apps))

    return {"apps": apps}


def _dockerfile_only_apps(
    docker: DockerService, agent: Agent, agent_id: str, gefunden: list[dict],
) -> list[dict]:
    """Verzeichnisse mit Dockerfile, aber ohne compose-Datei.

    Best effort: Faellt die Suche aus, bleibt es bei den compose-Apps — eine
    Zusatzinformation darf die Liste nie zum Scheitern bringen.
    """
    try:
        exit_code, stdout = docker.exec_in_container(
            agent.container_id,
            "find /workspace -maxdepth 3 -name 'Dockerfile'",
        )
    except Exception as e:  # noqa: BLE001 — Behaelter nicht erreichbar
        logger.info("discover_apps: Dockerfile-Suche fehlgeschlagen fuer %s: %s",
                    scrub_log(agent_id), e)
        return []
    if exit_code != 0 or not stdout.strip():
        return []

    bekannt = {a["path"] for a in gefunden}
    zusatz: list[dict] = []
    for pfad in stdout.strip().split("\n"):
        pfad = pfad.strip()
        if not pfad:
            continue
        verzeichnis = "/".join(pfad.split("/")[:-1])
        rel_path = verzeichnis.replace("/workspace/", "").replace("/workspace", "") or "."
        if rel_path in bekannt:
            continue
        bekannt.add(rel_path)
        zusatz.append({
            "name": rel_path.split("/")[-1] if rel_path != "." else "root",
            "path": rel_path,
            "compose_file": None,
            "services": [],
            "status": "needs_compose",
            "containers": [],
            "url": None,
            "hint": (
                "Nur ein Dockerfile, keine compose-Datei — dieses Projekt laesst "
                "sich so nicht bauen oder starten. Lege eine docker-compose.yml "
                "daneben (mindestens: services.<name>.build: .) und rufe dann "
                "rebuild_app mit diesem Pfad."
            ),
        })
    return zusatz


def _resolve_compose_file(docker: DockerService, agent: Agent, path: str, *, require: bool) -> str:
    """Return the path of the compose file in /workspace/<path>/, trying the 4 known
    names. If require=True and none exists, raise 404; else default to docker-compose.yml."""
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        candidate = f"/workspace/{path}/{name}"
        ec, _ = docker.exec_in_container(agent.container_id, f"test -f {shlex.quote(candidate)}")
        if ec == 0:
            return candidate
    if require:
        raise HTTPException(
            status_code=404, detail=f"No docker-compose file found in /workspace/{path}/",
        )
    return f"/workspace/{path}/docker-compose.yml"


def _ensure_env_files(docker: DockerService, agent: Agent, path: str, compose_file: str) -> None:
    """Create every env_file referenced by the compose (plus project-root .env) so
    compose does not hard-fail on a missing env_file target."""
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
        logger.debug("env_file scan failed for %s: %s", scrub_log(path), scrub_log(_e))
    for _rel in env_targets:
        full = f"/workspace/{path}/{_rel}"
        q = shlex.quote(full)
        docker.exec_in_container(
            agent.container_id,
            ["sh", "-c", f'f={q}; mkdir -p "$(dirname "$f")"; [ -d "$f" ] && rmdir "$f" 2>/dev/null; [ -e "$f" ] || touch "$f"'],
        )


async def _start_core(docker: DockerService, agent: Agent, agent_id: str, path: str) -> dict:
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    compose_file = _resolve_compose_file(docker, agent, path, require=True)
    project_name = _project_name(agent_id, path)
    workspace_volume = agent.volume_name or f"workspace-{agent_id}"

    _ensure_env_files(docker, agent, path, compose_file)

    logger.info(f"Starting Docker app: {scrub_log(project_name)} (path={scrub_log(path)}, agent={scrub_log(agent_id)})")

    # One-click: rewrite fixed host ports to auto-assigned free ones so a re-deploy
    # never fails on "port is already allocated".
    compose_file = await asyncio.to_thread(
        _prepare_free_port_compose, docker, agent, path, compose_file
    )

    async with _project_lock(project_name):
        exit_code, output = await _compose_with_conflict_recovery(
            docker, workspace_volume, project_name, compose_file,
            ["up", "-d", "--build"],
        )

    # Compose-Ausgabe kann Build-Argumente und Env-Dumps enthalten. `scrub_log`
    # entfernt nur Steuerzeichen (Log-Injection) — Geheimnisse maskiert erst
    # `redact_logs`. Und sie gehoert auch nicht in die Antwort an den Aufrufer,
    # sonst ist der Weg ueber HTTP offen, waehrend der Log dicht ist.
    output = redact_logs(output)
    if exit_code != 0:
        logger.error(f"Failed to start {scrub_log(project_name)}: {scrub_log(output)}")
        raise HTTPException(status_code=500, detail=f"Failed to start app: {output}")

    _connect_containers_to_network(docker, project_name)
    containers = _get_project_containers(docker, project_name)
    logger.info(f"Docker app started: {scrub_log(project_name)} ({len(containers)} containers)")
    return {"project": project_name, "status": "running", "containers": containers,
            "url": _app_public_url(agent_id, containers), "output": output}


async def _stop_core(docker: DockerService, agent: Agent, agent_id: str, path: str) -> dict:
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    project_name = _project_name(agent_id, path)
    workspace_volume = agent.volume_name or f"workspace-{agent_id}"
    compose_file = _resolve_compose_file(docker, agent, path, require=False)

    logger.info(f"Stopping Docker app: {scrub_log(project_name)}")
    async with _project_lock(project_name):
        exit_code, output = await asyncio.to_thread(
            _run_compose, docker, workspace_volume, project_name, compose_file, ["down"],
        )
    output = redact_logs(output)          # siehe _start_core
    if exit_code != 0:
        logger.warning(f"Compose down warning for {scrub_log(project_name)}: {scrub_log(output)}")
    return {"project": project_name, "status": "stopped", "output": output}


async def _rebuild_core(docker: DockerService, agent: Agent, agent_id: str, path: str) -> dict:
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    compose_file = _resolve_compose_file(docker, agent, path, require=False)
    project_name = _project_name(agent_id, path)
    workspace_volume = agent.volume_name or f"workspace-{agent_id}"

    docker.exec_in_container(agent.container_id, f"touch {shlex.quote(f'/workspace/{path}/.env')}")
    logger.info(f"Rebuilding Docker app: {scrub_log(project_name)}")

    compose_file = await asyncio.to_thread(
        _prepare_free_port_compose, docker, agent, path, compose_file
    )
    async with _project_lock(project_name):
        # Ein abgebrochener Recreate blockiert von allein jeden weiteren Versuch.
        await asyncio.to_thread(_reconcile_stale_backups, docker, project_name)
        exit_code, output = await _compose_with_conflict_recovery(
            docker, workspace_volume, project_name, compose_file,
            ["up", "-d", "--build", "--force-recreate"],
        )
    output = redact_logs(output)          # siehe _start_core
    if exit_code != 0:
        logger.error(f"Failed to rebuild {scrub_log(project_name)}: {scrub_log(output)}")
        raise HTTPException(status_code=500, detail=f"Failed to rebuild app: {output}")

    _connect_containers_to_network(docker, project_name)
    containers = _get_project_containers(docker, project_name)
    return {"project": project_name, "status": "running", "containers": containers,
            "url": _app_public_url(agent_id, containers), "output": output}


def _logs_core(docker: DockerService, agent_id: str, path: str, service: str | None, lines: int) -> dict:
    project_name = _project_name(agent_id, path)
    containers = _get_project_containers(docker, project_name)
    if not containers:
        return {"logs": [], "project": project_name}

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
                    logs.append({"service": container_info["service"], "line": line})
        except Exception as e:
            logs.append({"service": container_info["service"], "line": f"[Error reading logs: {e}]"})

    return {"logs": logs, "project": project_name, "total_lines": len(logs)}


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
    return await _discover_core(docker, agent, agent_id)


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
    return await _start_core(docker, agent, agent_id, path)


@router.post("/down")
async def stop_app(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Stop a docker-compose project."""
    agent = await _get_agent(agent_id, user, db)
    return await _stop_core(docker, agent, agent_id, path)


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
    return await asyncio.to_thread(_logs_core, docker, agent_id, path, service, lines)


@router.post("/rebuild")
async def rebuild_app(
    agent_id: str,
    path: str = Query(..., description="Relative path to project in /workspace"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Rebuild and restart a docker-compose project (forces image rebuild)."""
    agent = await _get_agent(agent_id, user, db)
    return await _rebuild_core(docker, agent, agent_id, path)


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
            logger.info(f"Restarted service {scrub_log(service)} in {scrub_log(project_name)}")
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

# Carries a public-link token across the app's sub-requests. Path-scoped to the one
# app it was issued for, HttpOnly, and never forwarded upstream (see `_strip` below)
# — the agent-authored app must not be able to read or replay it.
_SHARE_COOKIE = "ai_app_share"
_SHARE_COOKIE_MAX_AGE = 12 * 3600

# Query parameter carrying a public-link token on the very first hit. Deliberately
# NOT something short like `t`: this name is stripped before the request is proxied
# onwards, so it must not collide with a parameter the app itself uses.
_SHARE_QUERY = "__aie_share"


def _set_share_cookie(response: Response, request: Request, container: str, port: str, token: str) -> None:
    """Pin a public-link token to exactly ONE app's proxy path.

    Path-scoped so it never travels to another app, HttpOnly so no script (the
    app's own included) can read it, and never forwarded upstream.
    """
    idx = request.url.path.find("/proxy/")
    cookie_path = request.url.path[:idx] + f"/proxy/{container}/{port}/" if idx >= 0 else "/"
    response.set_cookie(
        _SHARE_COOKIE, token,
        max_age=_SHARE_COOKIE_MAX_AGE, path=cookie_path,
        httponly=True, samesite="lax",
        secure=(request.headers.get("x-forwarded-proto") or request.url.scheme) == "https",
    )


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
    user=Depends(optional_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Reverse-proxy an HTTP request to one of THIS agent's deployed app containers.

    Reachable at ``/agents/{id}/apps/proxy/{container}/{port}/…`` — served through the
    same Cloudflare+Caddy chain as the rest of ``/api/*`` (no exposed host port needed).

    WHERE it may point (the SSRF boundary) is decided by exactly ONE authoritative
    check: the container's ``com.docker.compose.project`` label must start with this
    agent's ``agent-{id8}-`` prefix. That label is set server-side by our own ``-p``,
    not by agent-authored code, so it cannot be forged. It is deliberately NOT keyed
    on the container's own NAME — apps may declare a fixed ``container_name`` that
    carries no prefix, and those must still work. The name is only sanitised against
    path traversal/injection before being used as an upstream host.

    WHO may pass (#467): the owner always; beyond that only what an ``AppShare`` on
    this exact compose project grants — named user, every logged-in user, or a public
    link token. Default stays deny. A share widens the *access path* only; the label
    gate above is unaffected by it, so sharing can never redirect to another target.
    """
    prefix = f"agent-{agent_id[:8]}-"
    # Injection guard on the name used as the upstream host (no path/traversal).
    if "/" in container or ".." in container or not container:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        raise HTTPException(status_code=400, detail="Invalid port")

    # Public-link token on the first hit, then carried by a path-scoped cookie so the
    # app's own sub-resources (JS/CSS/images) stay reachable without the query.
    query_token = (request.query_params.get(_SHARE_QUERY) or "").strip()
    share_token = query_token or (request.cookies.get(_SHARE_COOKIE) or "").strip()
    # Everything EXCEPT our own token goes upstream. Unconditional — the app must
    # never see the credential that gates it, no matter which method was used or
    # which share scope happened to grant access. Kept as pairs, so an app relying
    # on repeated keys (`?a=1&a=2`) still gets both.
    fwd_params = [(k, v) for k, v in request.query_params.multi_items() if k != _SHARE_QUERY]

    def _deny() -> HTTPException:
        """ONE answer for "no such container", "wrong project" and "not shared".

        Distinguishable statuses would let anyone holding a single share for this
        agent map out its OTHER apps by guessing container names. Only the owner
        gets the precise reason.
        """
        return HTTPException(
            status_code=401 if user is None else 403,
            detail="Not authenticated" if user is None else "Diese App ist nicht für dich freigegeben.",
        )

    is_owner = await is_app_owner(agent_id, user, db)
    if not is_owner and not await agent_has_active_shares(agent_id, db):
        # Nothing shared here at all — reject BEFORE touching Docker.
        raise _deny()

    # AUTHORITATIVE target check: the container must belong to a compose project we
    # started for THIS agent (the `com.docker.compose.project` label is set by our own
    # `-p agent-{id}-…`, not by agent-authored code, so it can't be forged). We key on
    # this label, NOT the container's own NAME — apps may set a fixed `container_name`
    # (e.g. `pokemon-tracker`) that doesn't carry the prefix, and those must still work.
    try:
        c = docker.client.containers.get(container)
    except Exception:
        if not is_owner:
            raise _deny()
        raise HTTPException(status_code=404, detail="App container not found")
    project = str(c.labels.get("com.docker.compose.project", ""))
    if not project.startswith(prefix):
        if not is_owner:
            raise _deny()
        raise HTTPException(status_code=403, detail="Forbidden")

    # Now that the target is pinned to a concrete project, resolve the caller's right
    # to open THAT project (owner short-circuits — no share lookup needed).
    access = ACCESS_OWNER if is_owner else await resolve_app_access(
        project, agent_id, user, share_token or None, db
    )

    # Public link, first hit: park the token in the path-scoped cookie and bounce to
    # the SAME url without the token. Otherwise the secret stays in `document.location`
    # and the browser hands it to the app in the `Referer` of every asset request —
    # i.e. straight to the agent-authored code the token is supposed to gate.
    # GET only: a 303 would rewrite any other method to GET. Everything else keeps
    # the token in the query for this one request and gets the cookie on the way out.
    if access == ACCESS_PUBLIC and query_token and request.method == "GET":
        target_url = request.url.path + (f"?{urlencode(fwd_params)}" if fwd_params else "")
        redirect = RedirectResponse(target_url, status_code=303)
        _set_share_cookie(redirect, request, container, port, share_token)
        return redirect

    # Route to the container's IP on the shared platform network, NOT its name:
    # compose-generated container names routinely exceed the 63-char DNS label limit
    # (e.g. "agent-<id>-<longpath>-<service>-1") and are then UNRESOLVABLE by Docker's
    # embedded DNS → the proxy 502s. The IP always works (both are on this network).
    # Fall back to the name for short-named containers that predate this.
    host = container
    try:
        nets = ((c.attrs or {}).get("NetworkSettings") or {}).get("Networks") or {}
        shared = nets.get("ai-employee-network") or {}
        ip = shared.get("IPAddress") or ""
        if not ip:
            for _n in nets.values():
                if _n.get("IPAddress"):
                    ip = _n["IPAddress"]
                    break
        if ip:
            host = ip
    except Exception:  # noqa: BLE001 — fall back to the name
        pass

    target = f"http://{host}:{int(port)}/{rest}"
    body = await request.body()
    # NEVER forward the platform auth credentials to the app — it runs agent-authored
    # code and could otherwise read the owner's session cookie / bearer token.
    # `referer` belongs in that list too: on a public link it would carry the share
    # token of whatever page issued the sub-request, handing the app the very
    # secret that gates it. The redirect above removes the token from the URL; this
    # closes the case where a referer reaches us anyway (bookmark, manual entry, an
    # older tab) so the token can never travel onwards.
    _strip = _HOP_BY_HOP | {"cookie", "authorization", "referer"}
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _strip
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            upstream = await client.request(
                request.method, target,
                params=fwd_params,
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
    # Belt and braces to the token-stripping redirect: even if a share token ends up
    # in the URL, the browser must not attach it as `Referer` anywhere.
    resp_headers["referrer-policy"] = "no-referrer"
    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
    # Non-GET first hits (a POST carrying the token) don't get the redirect above — set
    # the cookie here so their follow-up requests stay authorised.
    if access == ACCESS_PUBLIC and query_token:
        _set_share_cookie(response, request, container, port, share_token)
    return response
