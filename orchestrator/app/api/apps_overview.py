"""Global Apps overview — lists the running Docker apps a user is allowed to see.

Ownership (CRITICAL): a user only ever sees apps that belong to THEIR OWN agents.
App compose projects are named ``agent-{agentId8}-…`` (see docker_apps._project_name),
so we map each running app project back to an agent and keep only the ones the caller
owns. Admins see all. This is the platform-wide counterpart to the per-agent
``/agents/{id}/apps`` discovery — same ownership model, one screen.
"""

import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_sharing import (
    ACCESS_AUTHENTICATED,
    ACCESS_PUBLIC,
    ACCESS_USER,
    is_app_owner,
    shared_projects_for_user,
)
from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.models.agent import Agent
from app.models.app_share import APP_SHARE_SCOPES, AppShare, hash_share_token
from app.models.audit_log import AuditLog, AuditEventType
from app.models.user import User, UserRole
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apps", tags=["apps"])

#: Ein öffentlicher Link ist ein Loch in der Anmeldepflicht — er bekommt IMMER ein
#: Ablaufdatum, und länger als das hier geht nicht.
MAX_PUBLIC_SHARE_DAYS = 90


async def _visible_agents(user, db: AsyncSession) -> dict[str, Agent]:
    """Map ``agent-{id8}-`` project prefix → Agent, for every agent the user may see."""
    if getattr(user, "role", None) == UserRole.ADMIN:
        rows = (await db.execute(select(Agent))).scalars().all()
    else:
        rows = (await db.execute(
            select(Agent).where(Agent.user_id == str(getattr(user, "id", "")))
        )).scalars().all()
    return {f"agent-{a.id[:8]}-": a for a in rows}


async def _owner_names(agents, db: AsyncSession) -> dict[str, str]:
    """``user_id -> Anzeigename`` für die Besitzer der übergebenen Agenten.

    Eine Abfrage für alle. Bewusst nur der Name, nicht die Adresse: wem eine App
    freigegeben wurde, der soll wissen, von wem sie stammt — die Mailadresse des
    Besitzers geht ihn deshalb noch nicht an.
    """
    uids = {str(a.user_id) for a in agents if getattr(a, "user_id", None)}
    if not uids:
        return {}
    rows = (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all()
    return {r[0]: r[1] for r in rows}


async def _agent_for_project(project: str, db: AsyncSession) -> Agent:
    """Resolve the owning agent from a compose project name (``agent-{id8}-…``).

    Matching happens on the SAME prefix the project name was built from, so a
    project can never be attributed to an agent that didn't produce it.
    """
    if not project.startswith("agent-"):
        raise HTTPException(status_code=404, detail="App not found")
    rows = (await db.execute(select(Agent))).scalars().all()
    agent = next((a for a in rows if project.startswith(f"agent-{a.id[:8]}-")), None)
    if not agent:
        raise HTTPException(status_code=404, detail="App not found")
    return agent


async def _require_app_owner(project: str, user, db: AsyncSession) -> Agent:
    """Only the owner of the app's agent (or admin/manager) may MANAGE shares.

    Someone the app was shared with must never be able to re-share it, revoke a
    share, or control the containers — freigeben darf nur, wem die App gehört.
    """
    agent = await _agent_for_project(project, db)
    if not await is_app_owner(agent.id, user, db):
        raise HTTPException(status_code=404, detail="App not found")
    return agent


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
    # Apps, die MIR freigegeben wurden (#467) — ich sehe sie, darf sie aber nur
    # öffnen. Alle steuernden Endpunkte bleiben ownership-gated.
    shared = await shared_projects_for_user(user, db)
    if not owned and not shared:
        return {"apps": []}
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
                "containers": [], "url": None, "shared_with_me": None,
            })

    # 2. Actual containers (running/stopped) — taskforce apps + anything started.
    try:
        containers = docker.client.containers.list(
            all=True, filters={"label": "com.docker.compose.project"}
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[Apps] container list failed: %s", e)
        containers = []
    # Agenten hinter FREMDEN, mir freigegebenen Projekten nachladen (nur diese —
    # keine Vollabfrage, wenn nichts geteilt ist).
    shared_agents: dict[str, Agent] = {}
    if shared:
        for _a in (await db.execute(select(Agent))).scalars().all():
            pre = f"agent-{_a.id[:8]}-"
            if any(p.startswith(pre) for p in shared):
                shared_agents[pre] = _a

    for c in containers:
        proj = str(c.labels.get("com.docker.compose.project", ""))
        agent = next((a for pre, a in owned.items() if proj.startswith(pre)), None)
        share_scope = None
        if not agent and proj in shared:
            agent = next((a for pre, a in shared_agents.items() if proj.startswith(pre)), None)
            share_scope = shared[proj]
        if not agent:
            continue
        entry = apps.setdefault(proj, {
            "project": proj, "agent_id": agent.id, "agent_name": agent.name,
            "name": proj, "path": None, "status": "stopped",
            "containers": [], "url": None, "shared_with_me": share_scope,
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

    # Besitzer nachtragen. Für eigene Apps beantwortet das „wem gehört das
    # eigentlich" (Admins sehen alle), für freigegebene das eigentlich wichtige:
    # von wem stammt die App, die hier in meiner Liste auftaucht.
    all_agents = list(owned.values()) + list(shared_agents.values())
    names = await _owner_names(all_agents, db)
    by_agent = {
        a.id: (str(a.user_id or ""), names.get(str(a.user_id or ""), ""))
        for a in all_agents
    }
    me = str(getattr(user, "id", "") or "")
    for entry in apps.values():
        owner_id, owner_name = by_agent.get(entry["agent_id"], ("", ""))
        entry["owner_id"] = owner_id or None
        entry["owner_name"] = owner_name or None
        entry["owned_by_me"] = bool(owner_id) and owner_id == me

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


@router.post("/report")
async def report_app(
    project: str = Query(..., description="Compose project name"),
    body: dict = Body(default={}),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Report a failed app to its owning agent — dispatches a repair task with the
    error so the agent fixes it. Ownership-gated (the project must belong to the
    caller's agent). ``body`` may carry ``error`` (the start error) and ``path``
    (the workspace path of the app)."""
    from app.models.task import Task, TaskStatus, TaskPriority

    owned = await _visible_agents(user, db)
    agent = next((a for pre, a in owned.items() if project.startswith(pre)), None)
    if not agent:
        raise HTTPException(status_code=404, detail="App not found")

    error = str(body.get("error") or "").strip()[:4000] or "(kein Fehlertext übergeben)"
    path = str(body.get("path") or "").strip()
    where = f"/workspace/{path}" if path else f"das Compose-Projekt `{project}`"
    prompt = (
        f"Eine deiner Apps lässt sich **nicht starten** und wurde zur Reparatur gemeldet.\n\n"
        f"**App:** {path or project}\n"
        f"**Speicherort:** {where}\n\n"
        f"**Fehler beim Start (`docker compose up --build`):**\n```\n{error}\n```\n\n"
        "So gehst du vor:\n"
        f"1. **Analysieren:** Sieh dir in `{where}` das `Dockerfile`, die `docker-compose.yml` und referenzierte "
        "Dateien an. Verstehe die konkrete Ursache aus dem Fehler oben (z.B. fehlendes Paket/Datei, kaputter "
        "Build-Schritt, falscher Pfad, fehlende `.env`).\n"
        "2. **Beheben:** Korrigiere die Ursache direkt in den Projektdateien, sodass der Build/Start durchläuft.\n"
        "3. **Prüfen (im Rahmen deiner Rechte):** Wenn du Docker nutzen darfst, teste `docker compose build`; sonst "
        "validiere statisch (Syntax, Pfade, Dependencies).\n"
        "4. **Dokumentieren:** Halte kurz fest, was die Ursache war und was du geändert hast.\n"
        "Erfinde nichts — wenn eine Info fehlt, benenne konkret, was gebraucht wird."
    )
    task = Task(
        id=uuid.uuid4().hex[:12],
        title=f"[App-Reparatur] {path or project}",
        prompt=prompt,
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH,
        agent_id=agent.id,
        metadata_={"source": "app_repair", "project": project, "path": path},
    )
    db.add(task)
    await db.commit()

    # Push to the agent's queue + best-effort wake its container so it starts working.
    if redis.client:
        try:
            await redis.push_task(
                agent.id,
                json.dumps({"id": task.id, "prompt": prompt, "model": None, "priority": task.priority}),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[Apps] report push failed: %s", e)
    if agent.container_id:
        try:
            docker.start_container(agent.container_id)  # no-op if already running
        except Exception:  # noqa: BLE001
            pass

    return {"ok": True, "task_id": task.id, "agent_id": agent.id, "agent_name": agent.name}


# ══════════════════════════════════════════════════════════════════════════════
# FREIGABEN (#467)
#
# Default deny: ohne Eintrag kommt nur der Besitzer an eine App. Verwalten darf
# ausschließlich der Besitzer. Die Auswertung der Freigaben passiert NICHT hier,
# sondern zentral in ``core/app_sharing`` — dieselbe Logik, die der Proxy nutzt.
# ══════════════════════════════════════════════════════════════════════════════


class ShareCreate(BaseModel):
    scope: str = ACCESS_USER
    #: nur bei scope="user"
    user_id: str | None = None
    #: Pflicht bei scope="public", sonst optional (Tage ab jetzt)
    expires_in_days: int | None = None


def _share_dict(s: AppShare, name: str | None = None) -> dict:
    """Token bewusst NICHT hier — der geht nur einmal beim Anlegen zurück."""
    return {
        "id": s.id, "project": s.project, "scope": s.scope,
        "user_id": s.user_id, "user_name": name,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "expired": s.is_expired(),
        "has_token": bool(s.token_hash),
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/directory")
async def app_share_directory(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Minimale Nutzerliste (id/name/email) für den Freigabe-Dialog. Ohne den
    Aufrufer, ohne sensible Felder — analog zum Workflow-Freigabe-Picker."""
    rows = (await db.execute(select(User.id, User.name, User.email).order_by(User.name))).all()
    return {"users": [{"id": r[0], "name": r[1], "email": r[2]} for r in rows if r[0] != str(user.id)]}


@router.delete("/shares/{share_id}")
async def revoke_app_share(
    share_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Freigabe zurückziehen. Wirkt sofort — ein öffentlicher Link ist danach tot."""
    s = (await db.execute(select(AppShare).where(AppShare.id == share_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Freigabe nicht gefunden")
    agent = await _require_app_owner(s.project, user, db)
    db.add(AuditLog(
        agent_id=agent.id, event_type=AuditEventType.APP_SHARE_REVOKED,
        command=s.project, user_id=str(user.id),
        meta={"scope": s.scope, "grantee": s.user_id},   # nie der Token
    ))
    await db.delete(s)
    await db.commit()
    return {"deleted": share_id}


@router.get("/{project}/shares")
async def list_app_shares(
    project: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Alle Freigaben einer App — nur für den Besitzer sichtbar."""
    await _require_app_owner(project, user, db)
    rows = (await db.execute(select(AppShare).where(AppShare.project == project))).scalars().all()
    names: dict[str, str] = {}
    uids = [s.user_id for s in rows if s.user_id]
    if uids:
        for r in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all():
            names[r[0]] = r[1]
    return {"shares": [_share_dict(s, names.get(s.user_id or "")) for s in rows]}


@router.post("/{project}/shares", status_code=201)
async def create_app_share(
    project: str,
    body: ShareCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """App freigeben. Drei Stufen, aufsteigend nach Reichweite:

    * ``user``          — eine namentlich benannte Person (Login nötig)
    * ``authenticated`` — alle eingeloggten Plattform-Nutzer
    * ``public``        — Link mit Token, OHNE Login; Ablaufdatum ist Pflicht

    Der Token wird nur bei DIESEM Aufruf zurückgegeben und danach nie wieder
    ausgeliefert — wer den Link verliert, erzeugt einen neuen.
    """
    agent = await _require_app_owner(project, user, db)

    scope = (body.scope or "").strip().lower()
    if scope not in APP_SHARE_SCOPES:
        raise HTTPException(status_code=400, detail=f"scope muss einer von {APP_SHARE_SCOPES} sein")

    expires_at = None
    if scope == ACCESS_PUBLIC:
        days = body.expires_in_days or 7
        if days < 1 or days > MAX_PUBLIC_SHARE_DAYS:
            raise HTTPException(
                status_code=400,
                detail=f"Öffentliche Links laufen nach 1–{MAX_PUBLIC_SHARE_DAYS} Tagen ab.",
            )
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    elif body.expires_in_days:
        if body.expires_in_days < 1 or body.expires_in_days > 365:
            raise HTTPException(status_code=400, detail="Ablauf muss zwischen 1 und 365 Tagen liegen.")
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    token = None
    if scope == ACCESS_USER:
        target = (body.user_id or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="user_id fehlt")
        if not await db.scalar(select(User.id).where(User.id == target)):
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
        existing = (await db.execute(select(AppShare).where(
            AppShare.project == project, AppShare.scope == ACCESS_USER,
            AppShare.user_id == target,
        ))).scalar_one_or_none()
        if existing:
            existing.expires_at = expires_at
            await db.commit()
            return _share_dict(existing)
    elif scope == ACCESS_AUTHENTICATED:
        existing = (await db.execute(select(AppShare).where(
            AppShare.project == project, AppShare.scope == ACCESS_AUTHENTICATED,
        ))).scalar_one_or_none()
        if existing:
            existing.expires_at = expires_at
            await db.commit()
            return _share_dict(existing)
    else:
        token = secrets.token_urlsafe(32)

    s = AppShare(
        id=f"aps_{uuid.uuid4().hex[:12]}",
        project=project,
        agent_id=agent.id,
        scope=scope,
        user_id=(body.user_id or None) if scope == ACCESS_USER else None,
        token_hash=hash_share_token(token) if token else None,
        expires_at=expires_at,
        created_by=str(user.id),
    )
    db.add(s)
    db.add(AuditLog(
        agent_id=agent.id, event_type=AuditEventType.APP_SHARED,
        command=project, user_id=str(user.id),
        # Der Token gehört NIE ins Audit — er ist das ganze Geheimnis des Links.
        meta={"scope": scope, "grantee": s.user_id,
              "expires_at": expires_at.isoformat() if expires_at else None},
    ))
    await db.commit()
    logger.info("[Apps] share created project=%s scope=%s by=%s",
                scrub_log(project), scrub_log(scope), user.id)

    out = _share_dict(s)
    if token:
        out["token"] = token  # einmalig!
    return out


@router.get("/{project}")
async def app_detail(
    project: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """Detailansicht einer App: Container, Ports, Öffnen-Link und — für den
    Besitzer — die bestehenden Freigaben.

    Sichtbar für Besitzer UND für alle, denen die App freigegeben wurde. Wer sie
    nur freigegeben bekommen hat, sieht ``can_manage: false`` und keine
    Freigabe-Liste (sonst könnte er sehen, wer sonst noch Zugriff hat).
    """
    agent = await _agent_for_project(project, db)
    can_manage = await is_app_owner(agent.id, user, db)
    if not can_manage and project not in await shared_projects_for_user(user, db):
        raise HTTPException(status_code=404, detail="App not found")

    try:
        containers = docker.client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={project}"}
        )
    except Exception:  # noqa: BLE001
        containers = []

    out_containers, url, open_container, open_port = [], None, None, None
    for c in containers:
        port = _first_port(c)
        out_containers.append({
            "name": c.name,
            "service": c.labels.get("com.docker.compose.service", ""),
            "status": c.status,
            "image": (c.image.tags[0] if getattr(c.image, "tags", None) else ""),
            "port": port,
            "created": str(c.attrs.get("Created", ""))[:19],
        })
        if c.status == "running" and not url and port:
            url = f"/api/v1/agents/{agent.id}/apps/proxy/{c.name}/{port}/"
            open_container, open_port = c.name, port

    shares: list[dict] = []
    if can_manage:
        rows = (await db.execute(select(AppShare).where(AppShare.project == project))).scalars().all()
        names: dict[str, str] = {}
        uids = [s.user_id for s in rows if s.user_id]
        if uids:
            for r in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all():
                names[r[0]] = r[1]
        shares = [_share_dict(s, names.get(s.user_id or "")) for s in rows]

    owner_names = await _owner_names([agent], db)
    owner_id = str(agent.user_id or "")

    running = sum(1 for c in out_containers if c["status"] == "running")
    return {
        "project": project,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "owner_id": owner_id or None,
        "owner_name": owner_names.get(owner_id) or None,
        "owned_by_me": bool(owner_id) and owner_id == str(getattr(user, "id", "") or ""),
        "status": "running" if running and running == len(out_containers)
                  else "partial" if running else ("stopped" if out_containers else "not_started"),
        "containers": out_containers,
        "running": running,
        "total": len(out_containers),
        "url": url,
        "proxy_container": open_container,
        "proxy_port": open_port,
        "can_manage": can_manage,
        "shares": shares,
    }
