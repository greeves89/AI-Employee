import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AGENT_VERSION, settings
from app.core.agent_manager import DEFAULT_PERMISSIONS, AgentManager
from app.core.file_manager import FileManager
from app.core.realtime_catalog import IMPLEMENTED_ENGINES
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, is_agent_principal, require_auth, require_auth_or_agent, require_manager, verify_agent_token
from app.models.agent import Agent, AgentState
from app.security.agent_guard import check_inter_agent_message, notify_security_block
from app.models.chat_message import ChatMessage
from app.schemas.agent import AgentCreate, AgentListResponse, AgentModelUpdate, AgentResponse, BudgetExceededAction, KnowledgeResponse, KnowledgeUpdate, LLMConfigResponse, LLMConfigUpdate
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/agents", tags=["agents"])
logger = logging.getLogger(__name__)


def _mode_for_ai_account_provider(provider_type: str | None, requested_mode: str = "custom_llm") -> str:
    provider = (provider_type or "").lower()
    if provider == "anthropic":
        return "claude_code"
    if provider == "openai":
        return "codex_cli"
    return requested_mode if requested_mode in {"claude_code", "codex_cli"} else "custom_llm"


def _model_provider_for_agent_mode(mode: str, provider_type: str | None = None) -> str:
    if mode == "codex_cli":
        return "codex"
    if mode == "claude_code":
        return "anthropic"
    return (provider_type or settings.model_provider or "anthropic")


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


@router.get("/models")
async def get_model_catalog(user=Depends(require_auth)):
    """Provider/model catalog per harness (mode). The create modal and the
    per-agent settings render their provider + model dropdowns straight from
    this — one source of truth instead of hardcoded lists in three UI files."""
    from app.core.model_catalog import catalog_payload
    return catalog_payload()


@router.get("/logs")
async def read_agent_logs(
    target_agent_id: str | None = Query(None),
    tail: int = Query(200, ge=1, le=1000),
    since_minutes: int | None = Query(None, ge=1, le=1440),
    auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
):
    """An agent reads container logs to diagnose and improve itself.

    This is the SAFE path for the self-improvement loop — the orchestrator is the
    only component with docker access; the agent never touches the socket. Scope:
    an agent always reads its OWN logs; a team lead may also read the logs of its
    own team members. Output is secret-redacted (app.core.log_redaction) and every
    read is written to the audit log. tail is capped at 1000 lines.
    """
    caller_id = auth["agent_id"]
    target_id = target_agent_id or caller_id

    # Scope: own logs always; a lead may read its team members' logs.
    if target_id != caller_id:
        from app.models.team import Team
        teams = (await db.execute(select(Team).where(Team.is_active.is_(True)))).scalars().all()
        allowed = any(
            t.lead_agent_id == caller_id and target_id in (t.member_agent_ids or [])
            for t in teams
        )
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail="Zugriff nur auf eigene Logs — oder als Team-Lead auf die des eigenen Teams.",
            )

    target = (await db.execute(select(Agent).where(Agent.id == target_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not target.container_id:
        raise HTTPException(status_code=409, detail="Agent hat keinen laufenden Container")

    from app.core.log_redaction import redact_logs
    try:
        raw = docker.get_container_logs(
            target.container_id,
            tail=tail,
            since_seconds=since_minutes * 60 if since_minutes else None,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Logs konnten nicht gelesen werden: {e}")
    logs = redact_logs(raw)

    # Audit trail — who read whose logs, when, how much.
    from app.models.audit_log import AuditLog, AuditEventType
    db.add(AuditLog(
        agent_id=caller_id,
        event_type=AuditEventType.LOGS_READ.value,
        command="read_logs",
        outcome="success",
        meta={"target_agent_id": target_id, "tail": tail, "since_minutes": since_minutes},
    ))
    await db.commit()

    return {
        "agent_id": target_id,
        "tail": tail,
        "since_minutes": since_minutes,
        "logs": logs,
    }


# --- Team routes (MUST be before /{agent_id} to avoid path conflicts) ---


@router.get("/team/directory")
async def get_team_directory(
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Team directory. Users see their accessible agents; an agent sees its own
    team members + the leads of other teams (all agents when no teams exist)."""
    from app.models.user import UserRole
    agents = await manager.list_agents()
    if is_agent_principal(user):
        # An agent only sees its OWN team's members + the LEADS of other teams —
        # not every agent on the platform. If no teams are defined yet, behaviour
        # is unchanged (the agent still sees all) so nothing breaks pre-teams.
        from app.models.team import Team
        teams = (await db.execute(select(Team).where(Team.is_active.is_(True)))).scalars().all()
        if teams:
            my_team_members: set[str] = set()
            other_leads: set[str] = set()
            for t in teams:
                members = set(t.member_agent_ids or [])
                if user.id in members:
                    my_team_members |= members
                elif t.lead_agent_id:
                    other_leads.add(t.lead_agent_id)
            visible = my_team_members | other_leads | {user.id}
            agents = [a for a in agents if a.id in visible]
    elif hasattr(user, "role") and user.role != UserRole.ADMIN:
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
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Get recent inter-agent messages for visualization."""
    from datetime import timedelta
    from sqlalchemy import select, or_

    from app.models.agent_message import AgentMessage as AgentMessageModel

    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    # Higher limit for longer time windows to get accurate connection counts
    fetch_limit = 2000 if minutes > 1440 else 500 if minutes > 360 else 100

    query = select(AgentMessageModel).where(AgentMessageModel.timestamp >= since)
    if is_agent_principal(user):
        query = query.where(
            or_(
                AgentMessageModel.from_agent_id == user.id,
                AgentMessageModel.to_agent_id == user.id,
            )
        )
    else:
        # User principal: only messages involving the caller's own agents (admin = all).
        from app.core.ownership import visible_agent_ids
        vids = await visible_agent_ids(user, db)
        if vids is not None:
            if not vids:
                return {"connections": [], "messages": [], "total": 0}
            query = query.where(
                or_(
                    AgentMessageModel.from_agent_id.in_(list(vids)),
                    AgentMessageModel.to_agent_id.in_(list(vids)),
                )
            )
    query = query.order_by(AgentMessageModel.timestamp.desc()).limit(fetch_limit)
    result = await db.execute(query)
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


@router.get("/team/delegations")
async def get_agent_delegations(
    minutes: int = 1440,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Delegation edges: tasks one agent handed to another (delegator -> assignee).

    A delegated task carries a parent_task_id; the parent's agent_id is the
    delegator, the child task's own agent_id is the assignee. Grouped into edges
    for the agent-network graph.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, or_
    from sqlalchemy.orm import aliased
    from app.models.task import Task

    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    Parent = aliased(Task)
    deleg_where = [
        Task.created_at >= since,
        Task.agent_id.is_not(None),
        Parent.agent_id.is_not(None),
        Task.agent_id != Parent.agent_id,
    ]
    # Scope to the caller's own agents on either side of the edge (admin = all).
    if is_agent_principal(user):
        deleg_where.append(or_(Task.agent_id == user.id, Parent.agent_id == user.id))
    else:
        from app.core.ownership import visible_agent_ids
        vids = await visible_agent_ids(user, db)
        if vids is not None:
            if not vids:
                return {"edges": [], "total": 0}
            _v = list(vids)
            deleg_where.append(or_(Task.agent_id.in_(_v), Parent.agent_id.in_(_v)))
    rows = (await db.execute(
        select(Parent.agent_id, Task.agent_id, Task.title, Task.created_at)
        .join(Parent, Task.parent_task_id == Parent.id)
        .where(*deleg_where)
        .order_by(Task.created_at.desc())
        .limit(1000)
    )).all()

    edges: dict[tuple[str, str], dict] = {}
    for delegator, assignee, title, ts in rows:
        key = (delegator, assignee)
        if key not in edges:
            edges[key] = {
                "from": delegator,
                "to": assignee,
                "count": 0,
                "last_title": (title or "")[:60],
                "last_at": ts.isoformat() if ts else None,
            }
        edges[key]["count"] += 1
    return {"edges": list(edges.values()), "total": sum(e["count"] for e in edges.values())}


@router.get("/team/conversation")
async def get_agent_conversation(
    agent_a: str,
    agent_b: str,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Get the full conversation between two specific agents."""
    from sqlalchemy import select, or_, and_

    from app.models.agent_message import AgentMessage as AgentMessageModel

    if is_agent_principal(user):
        if user.id not in {agent_a, agent_b}:
            raise HTTPException(status_code=403, detail="Agents may only read their own conversations")
    else:
        # User principal: both agents must be the caller's own (admin = all).
        from app.core.ownership import visible_agent_ids
        vids = await visible_agent_ids(user, db)
        if vids is not None and not {agent_a, agent_b}.issubset(vids):
            raise HTTPException(status_code=404, detail="Not found")

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
    lite: bool = False,
    scope: str = "own",
    room_pool: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    from app.models.user import UserRole

    agents = await manager.list_agents()
    # Personal view (default): everyone — INCLUDING admins — sees only their own
    # agents (+ unowned + shared). The global "all agents" view is the Admin-Konsole,
    # which passes scope=all (admins only). room_pool=true additionally surfaces the
    # admin-curated agents (shared_for_rooms) to EVERY user — used by the Meeting-Room
    # agent picker so users don't each need to provision their own agents.
    is_admin = getattr(user, "role", None) == UserRole.ADMIN
    if not (is_admin and scope == "all"):
        from app.models.agent_access import AgentAccess
        access_result = await db.execute(
            select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
        )
        accessible_ids = {row[0] for row in access_result.all()}
        agents = [
            a for a in agents
            if a.user_id is None or a.user_id == user.id or a.id in accessible_ids
            or (room_pool and getattr(a, "shared_for_rooms", False))
        ]

    if lite:
        agent_responses = []
        for agent in agents:
            config = agent.config or {}
            safe_config = {
                "role": config.get("role", ""),
                "integrations": config.get("integrations", []),
                "permissions": config.get("permissions", DEFAULT_PERMISSIONS),
                "proactive": config.get("proactive"),
            }
            agent_responses.append(AgentResponse(
                id=agent.id,
                name=agent.name,
                container_id=agent.container_id,
                state=agent.state,
                model=agent.model or "",
                model_provider=config.get("model_provider", settings.model_provider),
                mode=agent.mode or "claude_code",
                role=config.get("role", ""),
                onboarding_complete=config.get("onboarding_complete", False),
                integrations=config.get("integrations", []),
                permissions=config.get("permissions", DEFAULT_PERMISSIONS),
                update_available=config.get("agent_version") != AGENT_VERSION,
                budget_usd=agent.budget_usd,
                budget_exceeded_action=agent.budget_exceeded_action,
                monthly_cost_usd=0.0,
                browser_mode=agent.browser_mode,
                autonomy_level=agent.autonomy_level or "l3",
                webhook_enabled=agent.webhook_enabled,
                webhook_token=agent.webhook_token,
                total_cost_usd=config.get("total_cost_usd", 0.0),
                user_id=agent.user_id,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
                config=safe_config,
                current_task="",
                queue_depth=0,
            ))
        return AgentListResponse(agents=agent_responses, total=len(agent_responses))

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
    # Any authenticated user may create agents; the per-role max_agents limit
    # (enforced below) governs how many — VIEWER roles have a limit of 0.
    user=Depends(require_auth),
    manager: AgentManager = Depends(_get_agent_manager),
    db: AsyncSession = Depends(get_db),
):
    try:
        account_provider_type = None

        # Validate: custom_llm mode requires an inline llm_config OR an AI account
        if data.mode == "custom_llm" and not data.llm_config and not data.ai_account_id:
            raise HTTPException(
                status_code=422,
                detail="custom_llm mode requires either llm_config or ai_account_id",
            )

        # An AI account drives the harness: Anthropic -> Claude Code,
        # OpenAI -> Codex CLI, local/other APIs -> custom harness.
        if data.ai_account_id:
            from app.models.ai_account import AIAccount
            account = await db.get(AIAccount, data.ai_account_id)
            if not account:
                raise HTTPException(status_code=422, detail="AI account not found")
            if not account.is_active:
                raise HTTPException(status_code=422, detail="AI account is inactive")
            if not account.models:
                raise HTTPException(status_code=422, detail="AI account has no models configured")
            account_provider_type = account.provider_type
            _model_names = [m.get("name") if isinstance(m, dict) else m for m in account.models]
            if data.model and data.model not in _model_names:
                raise HTTPException(
                    status_code=422,
                    detail=f"Model '{data.model}' is not offered by this AI account",
                )

        # Role-based permission checks (skip for setup-mode anonymous user)
        if user.id != "__anonymous__":
            from app.core.permissions import (
                get_effective_permissions,
                can_use_llm_provider,
                can_use_model,
            )
            from sqlalchemy import select, func
            from app.models.agent import Agent as _Agent

            perms = await get_effective_permissions(user, db)
            # 1) Max agents
            max_agents = perms.get("max_agents")
            if max_agents is not None:
                count = (await db.execute(
                    select(func.count(_Agent.id)).where(_Agent.user_id == user.id)
                )).scalar() or 0
                if count >= max_agents:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Agent-Limit erreicht ({max_agents}). Bitte einen bestehenden Agent löschen.",
                    )
            # 2) AI-Account: default-deny — a non-admin may only bind an account that
            #    was explicitly released to them (admin-aware; mirrors GET /ai-accounts).
            if data.ai_account_id is not None:
                from app.api.ai_accounts import _allowed_account_ids
                allowed_acc = await _allowed_account_ids(user, db)  # None = admin/all
                if allowed_acc is not None and data.ai_account_id not in allowed_acc:
                    raise HTTPException(
                        status_code=403,
                        detail="Dieser AI-Account ist für deine Gruppe nicht freigegeben.",
                    )
            # 3) LLM provider whitelist — only for the manual/custom path. When a
            #    (granted) AI-Account is chosen, the account grant is the
            #    authorization; its provider string (e.g. azure-openai) must NOT be
            #    re-checked against the role's llm_providers list.
            if data.ai_account_id is None:
                llm_type = data.llm_config.provider_type if data.llm_config else account_provider_type
                if not can_use_llm_provider(perms, llm_type):
                    raise HTTPException(
                        status_code=403,
                        detail=f"LLM-Provider '{llm_type}' ist für deine Rolle nicht erlaubt.",
                    )
            # 4) Model allowlist — a group may be limited to specific models
            #    (admin-safe: admins resolve to models=None → unrestricted). Applies
            #    to both the account and the manual path (it gates the model NAME).
            if data.model and not can_use_model(perms, data.model):
                raise HTTPException(
                    status_code=403,
                    detail=f"Modell '{data.model}' ist für deine Gruppe nicht freigegeben.",
                )

        # Guard: the model must belong to the harness that will run it. A
        # claude_code agent may only use Claude models, a codex_cli agent only
        # GPT/o-series — otherwise the CLI fails at runtime ("claude model not
        # supported with a ChatGPT account"). custom_llm is exempt (its model
        # comes from the account/llm_config). Single source: model_catalog.
        final_mode = _mode_for_ai_account_provider(account_provider_type, data.mode)
        if data.ai_account_id is None and data.model:
            from app.core.model_catalog import is_model_allowed_for_mode, default_model_for_mode
            if not is_model_allowed_for_mode(final_mode, data.model):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Modell '{data.model}' passt nicht zum Provider "
                        f"'{final_mode}'. Erlaubt sind z. B. "
                        f"'{default_model_for_mode(final_mode)}'."
                    ),
                )

        # Don't set user_id for anonymous (setup mode) users
        uid = user.id if user.id != "__anonymous__" else None
        agent = await manager.create_agent(
            name=data.name, model=data.model, role=data.role,
            integrations=data.integrations, permissions=data.permissions,
            user_id=uid, budget_usd=data.budget_usd,
            budget_exceeded_action=data.budget_exceeded_action,
            mode=final_mode,
            llm_config=data.llm_config.model_dump() if data.llm_config else None,
            ai_account_id=data.ai_account_id,
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
    from app.services.agent_settings import change_autonomy_level
    return await change_autonomy_level(db, user, agent_id, data.level)


class ParallelSessionsUpdate(BaseModel):
    parallel_sessions: int


@router.post("/{agent_id}/parallel-sessions")
async def set_parallel_sessions(
    agent_id: str,
    data: ParallelSessionsUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set how many sessions (tasks + chats) the agent runs in parallel; beyond
    that, work queues. Recreates the container so the new limit takes effect."""
    from app.services.agent_settings import change_parallel_sessions
    return await change_parallel_sessions(db, user, agent_id, data.parallel_sessions, manager)


@router.get("/{agent_id}/autonomy-matrix")
async def get_autonomy_matrix(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Capability taxonomy + the agent's current 3-state autonomy matrix."""
    await _check_owner(agent_id, user, db)
    from app.core import autonomy_matrix as am
    agent = (await db.execute(
        select(Agent.autonomy_level, Agent.config).where(Agent.id == agent_id)
    )).first()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    level = (agent[0] or "l3").lower()
    matrix = am.normalize_matrix((agent[1] or {}).get("autonomy_matrix"), level)
    return {
        "agent_id": agent_id,
        "autonomy_level": level,
        "matrix": matrix,
        "taxonomy": am.taxonomy_payload(),
    }


class AutonomyMatrixUpdate(BaseModel):
    matrix: dict[str, str]


@router.put("/{agent_id}/autonomy-matrix")
async def update_autonomy_matrix(
    agent_id: str,
    body: AutonomyMatrixUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Set a custom 3-state matrix (fine-tuning after a preset). Level → 'custom'
    when the matrix no longer matches its L1–L4 preset."""
    await _check_owner(agent_id, user, db)
    from app.core import autonomy_matrix as am
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    level = (agent.autonomy_level or "l3").lower()
    matrix = am.normalize_matrix(body.matrix, level)
    # If the edited matrix still equals a preset, keep that level label; else custom.
    matched = next((lvl for lvl in ("l1", "l2", "l3", "l4")
                    if am.matrix_for_level(lvl) == matrix), None)
    agent.config = {**(agent.config or {}), "autonomy_matrix": matrix}
    if matched:
        agent.autonomy_level = matched
    elif agent.autonomy_level in ("l1", "l2", "l3", "l4"):
        agent.autonomy_level = "custom"
    await db.commit()
    return {
        "agent_id": agent_id,
        "autonomy_level": agent.autonomy_level,
        "matrix": matrix,
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


@router.patch("/{agent_id}/room-sharing")
async def set_room_sharing(
    agent_id: str,
    shared_for_rooms: bool = Body(..., embed=True),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: add/remove an agent from the Meeting-Room shared pool. Pooled
    agents appear in EVERY user's room agent picker, so an admin can pre-provision a
    ready-to-use set of agents instead of each user bringing their own."""
    from app.models.user import UserRole
    if getattr(user, "role", None) != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin only")
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    # DATA-LEAK GUARD: only "standard" agents may enter the shared pool — i.e. agents
    # with no personal owner, or owned by the acting admin. A regular user's personal
    # agent carries that user's accumulated knowledge/memory; exposing it to everyone
    # via the room pool would leak their data. Such agents cannot be pooled.
    if shared_for_rooms and agent.user_id and str(agent.user_id) != str(user.id):
        raise HTTPException(
            status_code=400,
            detail="Nur Standard-Agenten (ohne persönlichen Besitzer) können freigegeben "
                   "werden. Ein persönlich erstellter Agent trägt das Wissen seines Erstellers "
                   "und darf nicht für alle freigegeben werden.",
        )
    agent.shared_for_rooms = bool(shared_for_rooms)
    await db.commit()
    return {"id": agent.id, "shared_for_rooms": agent.shared_for_rooms}


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
        provider_type = body.provider_type or (agent.llm_config or {}).get("provider_type")
        from app.core.permissions import get_effective_permissions, can_use_llm_provider
        perms = await get_effective_permissions(user, db)
        if not can_use_llm_provider(perms, provider_type):
            raise HTTPException(
                status_code=403,
                detail=f"LLM-Provider '{provider_type}' ist für deine Rolle nicht erlaubt.",
            )

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
    from app.services.agent_settings import change_agent_model
    try:
        result = await change_agent_model(
            db, user, agent_id, body.model, body.model_provider, manager
        )
        return {**result, "status": "updated"}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class AgentAppearanceUpdate(BaseModel):
    icon: str | None = None
    color: str | None = None


# Curated lucide icon set + color tokens the UI offers — block arbitrary values
# (avatar is rendered client-side by mapping these names to lucide components).
_AVATAR_ICONS = {
    "Bot", "Cpu", "Brain", "Sparkles", "Rocket", "Briefcase", "Cog",
    "MessageSquare", "Code", "Database", "Mail", "Calendar", "FileText",
    "Headphones", "ShieldCheck", "Stethoscope", "FlaskConical", "Bug",
}
_AVATAR_COLORS = {
    "violet", "blue", "emerald", "amber", "rose", "cyan", "fuchsia", "slate", "orange",
}


@router.patch("/{agent_id}/appearance")
async def update_agent_appearance(
    agent_id: str,
    body: AgentAppearanceUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set the agent's custom icon + color (cosmetic — stored in config, no restart)."""
    await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        config = dict(agent.config or {})
        avatar = dict(config.get("avatar") or {})
        if body.icon is not None:
            if body.icon and body.icon not in _AVATAR_ICONS:
                raise HTTPException(status_code=400, detail="Unknown icon")
            avatar["icon"] = body.icon
        if body.color is not None:
            if body.color and body.color not in _AVATAR_COLORS:
                raise HTTPException(status_code=400, detail="Unknown color")
            avatar["color"] = body.color
        config["avatar"] = avatar
        agent.config = config
        await db.commit()
        return {"agent_id": agent_id, "avatar": avatar, "status": "updated"}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class AgentRenameUpdate(BaseModel):
    name: str


@router.patch("/{agent_id}/name")
async def rename_agent(
    agent_id: str,
    body: AgentRenameUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Rename an agent (display name only — no container restart).

    The Docker container keeps its original name (addressed by container ID); only
    the DB display name + team registry entry change. The container name is re-derived
    from this name ONLY on the next (re)creation, via a safe slug.
    """
    await _check_owner(agent_id, user, db)
    # Sanitize untrusted input: trim, drop control/non-printable chars, cap length.
    name = "".join(ch for ch in (body.name or "").strip() if ch.isprintable())
    if not name:
        raise HTTPException(status_code=422, detail="Name darf nicht leer sein")
    if len(name) > 40:
        raise HTTPException(status_code=422, detail="Name darf höchstens 40 Zeichen haben")
    try:
        agent = await manager._get_agent(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.name = name
    await db.commit()
    # Keep the team registry display name in sync so team listings show the new name
    # immediately, without recreating the container.
    try:
        if agent.container_id:
            role = (agent.config or {}).get("role") or "Unassigned"
            manager._update_team_registry(agent.container_id, agent_id, name, role)
    except Exception:  # noqa: BLE001 — registry sync is best-effort, never blocks rename
        logger.warning("team registry name sync failed for agent=%s", agent_id, exc_info=True)
    return {"agent_id": agent_id, "name": name, "status": "updated"}


class AgentBudgetUpdate(BaseModel):
    budget_usd: float | None  # None = unlimited
    budget_exceeded_action: BudgetExceededAction | None = None


@router.patch("/{agent_id}/budget")
async def update_agent_budget(
    agent_id: str,
    body: AgentBudgetUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set or clear the monthly budget cap + over-budget action.

    Budget is an admin governance control: only admins may set it; owners see it
    read-only in the agent settings.
    """
    from app.models.user import UserRole
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Nur Admins können das Budget festlegen.")
    try:
        agent = await manager._get_agent(agent_id)
        agent.budget_usd = body.budget_usd
        if body.budget_exceeded_action is not None:
            agent.budget_exceeded_action = body.budget_exceeded_action
        await db.commit()
        return {
            "agent_id": agent_id,
            "budget_usd": agent.budget_usd,
            "budget_exceeded_action": agent.budget_exceeded_action,
            "status": "updated",
        }
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


class AgentAIAccountUpdate(BaseModel):
    ai_account_id: int       # the AI account to connect this agent to
    model: str | None = None  # which model from the account (defaults to first)


@router.patch("/{agent_id}/ai-account")
async def update_agent_ai_account(
    agent_id: str,
    body: AgentAIAccountUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Connect an agent to a (different) AI account and recreate its container."""
    await _check_owner(agent_id, user, db)
    from app.models.ai_account import AIAccount

    account = await db.get(AIAccount, body.ai_account_id)
    if not account:
        raise HTTPException(status_code=404, detail="AI account not found")
    # Default-deny: owning the agent is not enough — the caller must also be allowed
    # to USE this AI account (else a user could bind their agent to a foreign/shared one).
    from app.api.ai_accounts import _allowed_account_ids
    allowed_acc = await _allowed_account_ids(user, db)  # None = admin/all
    if allowed_acc is not None and body.ai_account_id not in allowed_acc:
        raise HTTPException(status_code=404, detail="AI account not found")
    if not account.is_active:
        raise HTTPException(status_code=422, detail="AI account is inactive")
    if not account.models:
        raise HTTPException(status_code=422, detail="AI account has no models configured")
    _model_names = [m.get("name") if isinstance(m, dict) else m for m in account.models]
    chosen_model = body.model or _model_names[0]
    if chosen_model not in _model_names:
        raise HTTPException(
            status_code=422,
            detail=f"Model '{chosen_model}' is not offered by this AI account",
        )
    try:
        agent = await manager._get_agent(agent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")

    mode = _mode_for_ai_account_provider(account.provider_type)
    config = dict(agent.config or {})
    config["model_provider"] = _model_provider_for_agent_mode(mode, account.provider_type)

    agent.ai_account_id = account.id
    agent.mode = mode
    agent.model = chosen_model
    agent.config = config
    await db.commit()

    # Recreate the container so the account's provider/credentials take effect.
    try:
        await manager.update_agent(agent_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"AI account set, but container recreate failed for {agent_id}: {e}")

    return {"agent_id": agent_id, "ai_account_id": account.id, "status": "updated"}


@router.patch("/{agent_id}/idle-stop")
async def update_agent_idle_stop(
    agent_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set the per-agent idle-stop minutes (capped at the admin-global maximum).

    Body: {"idle_stop_minutes": int | null}
    null/0 → no per-agent override (still subject to global limit if set).
    """
    await _check_owner(agent_id, user, db)
    from app.models.platform_settings import PlatformSettings

    raw = body.get("idle_stop_minutes")
    try:
        minutes = int(raw) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="idle_stop_minutes must be an integer")
    if minutes < 0:
        raise HTTPException(status_code=422, detail="idle_stop_minutes must be >= 0")

    # Cap at global admin maximum (if set)
    ps = await db.get(PlatformSettings, "max_idle_minutes")
    try:
        global_max = int(ps.value) if ps and ps.value else 0
    except Exception:
        global_max = 0
    if global_max > 0 and minutes > global_max:
        raise HTTPException(
            status_code=422,
            detail=f"idle_stop_minutes ({minutes}) exceeds global maximum ({global_max})",
        )

    try:
        agent = await manager._get_agent(agent_id)
        cfg = dict(agent.config or {})
        if minutes == 0:
            cfg.pop("idle_stop_minutes", None)
        else:
            cfg["idle_stop_minutes"] = minutes
        agent.config = cfg
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()
        return {"agent_id": agent_id, "idle_stop_minutes": minutes if minutes > 0 else None}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# Realtime voice interaction engines the agent can front with (besides the classic
# staged STT→LLM→TTS pipeline). Single source of truth is the realtime catalog, so
# the selector, session backends and this allowlist never drift apart — previously
# this was hardcoded to {"nova_sonic"}, which 422'd every Azure gpt-realtime pick.
INTERACTION_MODELS = IMPLEMENTED_ENGINES


@router.put("/{agent_id}/interaction-model")
async def update_agent_interaction_model(
    agent_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    """Set the agent's realtime voice interaction front.

    Body: {"interaction_model": "nova_sonic" | null, "interaction_account_id": int | null,
           "interaction_model_id": str | null, "interaction_voice": str | null}
    null / "" interaction_model → classic staged STT→LLM→TTS pipeline (default).
    interaction_account_id links the AWS/Azure AI-Account whose creds to use;
    interaction_model_id is the concrete provider model (e.g. amazon.nova-2-sonic-v1:0).
    """
    await _check_owner(agent_id, user, db)
    model = (body.get("interaction_model") or "").strip() or None
    if model is not None and model not in INTERACTION_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"interaction_model must be one of {sorted(INTERACTION_MODELS)} or null",
        )
    voice = (body.get("interaction_voice") or "").strip() or None
    raw_acc = body.get("interaction_account_id")
    account_id = int(raw_acc) if raw_acc not in (None, "", 0, "0") else None
    model_id = (body.get("interaction_model_id") or "").strip() or None
    # AuthZ: only link an AI-Account the user is actually allowed to use.
    if account_id is not None:
        from app.models.ai_account import AIAccount
        from app.api.ai_accounts import _allowed_account_ids
        acc = (await db.execute(
            select(AIAccount).where(AIAccount.id == account_id, AIAccount.is_active.is_(True))
        )).scalar_one_or_none()
        allowed_ids = await _allowed_account_ids(user, db)
        if not acc or (allowed_ids is not None and account_id not in allowed_ids):
            raise HTTPException(status_code=403, detail="AI-Account nicht zugänglich")
    try:
        agent = await manager._get_agent(agent_id)
        cfg = dict(agent.config or {})
        for k in ("interaction_model", "interaction_voice",
                  "interaction_account_id", "interaction_model_id"):
            cfg.pop(k, None)
        if model is not None:
            cfg["interaction_model"] = model
            if voice:
                cfg["interaction_voice"] = voice
            if account_id:
                cfg["interaction_account_id"] = account_id
            if model_id:
                cfg["interaction_model_id"] = model_id
        agent.config = cfg
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()
        return {
            "agent_id": agent_id, "interaction_model": model,
            "interaction_account_id": account_id, "interaction_model_id": model_id,
            "interaction_voice": voice,
        }
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
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"Failed to remove agent {agent_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove agent: {type(e).__name__}: {e}",
        )


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


def _decode_redis_hash(data: dict) -> dict:
    decoded = {}
    for key, value in (data or {}).items():
        k = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
        v = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        decoded[k] = v
    return decoded


def _is_busy_with_task(status: dict) -> bool:
    state = str(status.get("state") or "")
    current_task = str(status.get("current_task") or "")
    if state != "working":
        return False
    return bool(current_task) and not current_task.startswith(("chat:", "msg:"))


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
    if not is_agent_principal(user):
        await _check_owner(agent_id, user, db)
    try:
        agent = await manager._get_agent(agent_id)
        if not agent.container_id:
            raise HTTPException(status_code=400, detail="Agent has no container")

        target_status = _decode_redis_hash(await redis.client.hgetall(f"agent:{agent_id}:status"))
        target_busy = _is_busy_with_task(target_status)

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
        if is_agent_principal(user) and body.from_agent_id:
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

        return {
            "status": "deferred" if target_busy else "sent",
            "message_id": message_id,
            "to_agent": agent_id,
            "target_state": target_status.get("state"),
            "target_current_task": target_status.get("current_task"),
            "will_reply_later": target_busy,
        }
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
    from sqlalchemy import func
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

    session_ids = [s.session_id for s in sessions]
    previews = {session_id: "" for session_id in session_ids}
    if session_ids:
        user_preview = (
            select(
                ChatMessage.session_id.label("session_id"),
                ChatMessage.content.label("content"),
                func.row_number()
                .over(partition_by=ChatMessage.session_id, order_by=ChatMessage.id.asc())
                .label("row_num"),
            )
            .where(ChatMessage.agent_id == agent_id)
            .where(ChatMessage.session_id.in_(session_ids))
            .where(ChatMessage.role == "user")
            .subquery()
        )
        user_preview_result = await db.execute(
            select(user_preview.c.session_id, user_preview.c.content)
            .where(user_preview.c.row_num == 1)
        )
        for session_id, content in user_preview_result.all():
            previews[session_id] = (content or "")[:80]

        assistant_preview = (
            select(
                ChatMessage.session_id.label("session_id"),
                ChatMessage.content.label("content"),
                ChatMessage.meta.label("meta"),
                func.row_number()
                .over(partition_by=ChatMessage.session_id, order_by=ChatMessage.id.desc())
                .label("row_num"),
            )
            .where(ChatMessage.agent_id == agent_id)
            .where(ChatMessage.session_id.in_(session_ids))
            .where(ChatMessage.role == "assistant")
            .subquery()
        )
        assistant_preview_result = await db.execute(
            select(
                assistant_preview.c.session_id,
                assistant_preview.c.content,
                assistant_preview.c.meta,
            )
            .where(assistant_preview.c.row_num == 1)
        )
        for session_id, content, meta in assistant_preview_result.all():
            if previews.get(session_id):
                continue
            preview = content or ""
            files = meta.get("presented_files") if isinstance(meta, dict) else None
            if not preview and files:
                first_file = files[0] if isinstance(files, list) and files else {}
                if isinstance(first_file, dict):
                    preview = first_file.get("filename") or first_file.get("path") or ""
            previews[session_id] = preview[:80]

    # Filter out phantom sessions (no user messages, only empty assistant entries)
    valid_sessions = [
        s for s in sessions
        if previews.get(s.session_id) or s.message_count > 1 or s.session_id == "scheduler"
    ]
    # Fall back to all sessions if filtering removed everything
    if not valid_sessions:
        valid_sessions = list(sessions)

    # Merge in per-session metadata (custom title + pin). A session without a
    # row keeps its derived preview and is unpinned — nothing breaks pre-feature.
    from app.models.chat_session import ChatSession
    meta_rows = (await db.execute(
        select(ChatSession.session_id, ChatSession.title, ChatSession.pinned)
        .where(ChatSession.agent_id == agent_id)
    )).all()
    meta = {m.session_id: (m.title, m.pinned) for m in meta_rows}

    out = []
    for s in valid_sessions:
        title, pinned = meta.get(s.session_id, (None, False))
        out.append({
            "id": s.session_id,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            "message_count": s.message_count,
            "preview": previews.get(s.session_id, ""),
            "title": title,
            "pinned": bool(pinned),
        })
    # Pinned first, then keep the existing recency order (query already desc).
    out.sort(key=lambda x: 0 if x["pinned"] else 1)
    return {"sessions": out}


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
    query = query.order_by(ChatMessage.id.desc()).limit(limit + 1)
    result = await db.execute(query)
    fetched_messages = result.scalars().all()
    has_more = len(fetched_messages) > limit
    messages = fetched_messages[:limit]
    next_before_id = min((msg.id for msg in messages), default=None)
    # Return in chronological order (oldest first)
    messages = list(reversed(messages))

    deduped = []
    seen: set[tuple[str, str, str]] = set()
    for msg in messages:
        key = (msg.message_id or str(msg.id), msg.role, msg.content or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(msg)

    def _normalise_tool_calls(tool_calls):
        if not isinstance(tool_calls, list):
            return tool_calls
        normalised = []
        for call in tool_calls:
            if not isinstance(call, dict):
                normalised.append(call)
                continue
            fixed = dict(call)
            raw_input = fixed.get("input")
            if isinstance(raw_input, str):
                try:
                    fixed["input"] = json.loads(raw_input)
                except (json.JSONDecodeError, TypeError):
                    fixed["input"] = {"raw": raw_input}
            normalised.append(fixed)
        return normalised

    return {
        "messages": [
            {
                "id": str(msg.id),
                "message_id": msg.message_id,
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "toolCalls": _normalise_tool_calls(msg.tool_calls),
                "meta": msg.meta,
                "sessionId": msg.session_id,
            }
            for msg in deduped
        ],
        "has_more": has_more,
        "next_before_id": next_before_id,
    }


@router.delete("/{agent_id}/chat/sessions/{session_id}")
async def delete_chat_session(
    agent_id: str,
    session_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete all messages in a chat session. Pinned sessions are protected."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy import delete as sql_delete, select
    from app.models.chat_session import ChatSession
    cs = (await db.execute(
        select(ChatSession)
        .where(ChatSession.agent_id == agent_id, ChatSession.session_id == session_id)
    )).scalar_one_or_none()
    if cs and cs.pinned:
        raise HTTPException(
            status_code=409,
            detail="Angepinnter Chat kann nicht gelöscht werden. Löse den Pin zuerst.",
        )
    result = await db.execute(
        sql_delete(ChatMessage)
        .where(ChatMessage.agent_id == agent_id)
        .where(ChatMessage.session_id == session_id)
    )
    await db.execute(
        sql_delete(ChatSession)
        .where(ChatSession.agent_id == agent_id)
        .where(ChatSession.session_id == session_id)
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.delete("/{agent_id}/chat/sessions")
async def delete_all_chat_sessions(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete ALL chat sessions (messages + metadata) for an agent — EXCEPT pinned ones."""
    await _check_owner(agent_id, user, db)
    from sqlalchemy import delete as sql_delete, select
    from app.models.chat_session import ChatSession
    pinned_ids = list((await db.execute(
        select(ChatSession.session_id)
        .where(ChatSession.agent_id == agent_id, ChatSession.pinned.is_(True))
    )).scalars().all())
    del_msgs = sql_delete(ChatMessage).where(ChatMessage.agent_id == agent_id)
    if pinned_ids:
        del_msgs = del_msgs.where(ChatMessage.session_id.notin_(pinned_ids))
    result = await db.execute(del_msgs)
    # Keep pinned session rows; drop the rest.
    await db.execute(
        sql_delete(ChatSession)
        .where(ChatSession.agent_id == agent_id, ChatSession.pinned.is_(False))
    )
    await db.commit()
    return {"deleted": result.rowcount, "kept_pinned": len(pinned_ids)}


class ChatSessionUpdate(BaseModel):
    title: str | None = None   # non-empty → set custom title; "" / null → clear to derived preview
    pinned: bool | None = None


@router.patch("/{agent_id}/chat/sessions/{session_id}")
async def update_chat_session(
    agent_id: str,
    session_id: str,
    body: ChatSessionUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Rename and/or pin a chat session. Upserts the metadata row lazily."""
    await _check_owner(agent_id, user, db)
    from app.models.chat_session import ChatSession
    row = (await db.execute(
        select(ChatSession).where(
            ChatSession.agent_id == agent_id,
            ChatSession.session_id == session_id,
        )
    )).scalar_one_or_none()
    if row is None:
        row = ChatSession(agent_id=agent_id, session_id=session_id)
        db.add(row)
    if body.title is not None:
        cleaned = body.title.strip()[:120]
        row.title = cleaned or None
    if body.pinned is not None:
        row.pinned = body.pinned
    await db.commit()
    return {
        "id": session_id,
        "title": row.title,
        "pinned": bool(row.pinned),
    }


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
        return {
            "agent_id": agent_id,
            "integrations": config.get("integrations", []),
            "msgraph_access": (config or {}).get("msgraph_access", "read"),
            # Was missing → the UI never saw the saved value and always fell back to
            # "read", so Exchange Read+Write looked like it reset itself after refresh.
            "exchange_access": (config or {}).get("exchange_access", "read"),
        }
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
        # AuthZ: the user's role may restrict which integration providers (M365,
        # Exchange, …) its agents can enable. Only enforce on newly-added providers
        # so an admin-preconfigured integration isn't wiped by a later save.
        from app.core.permissions import get_effective_permissions, can_use_integration
        perms = await get_effective_permissions(user, db)
        for prov in set(new_integrations) - old_integrations:
            if not can_use_integration(perms, prov):
                raise HTTPException(
                    status_code=403,
                    detail=f"Integration '{prov}' ist für deine Rolle nicht freigegeben",
                )
        config["integrations"] = new_integrations

        # Optional: Microsoft Graph read/write mode for this agent.
        old_msgraph_access = config.get("msgraph_access", "read")
        if "msgraph_access" in body and body["msgraph_access"] in ("read", "write"):
            config["msgraph_access"] = body["msgraph_access"]
        msgraph_access_changed = config.get("msgraph_access", "read") != old_msgraph_access

        # Optional: on-prem Exchange read/write mode for this agent.
        old_exchange_access = config.get("exchange_access", "read")
        if "exchange_access" in body and body["exchange_access"] in ("read", "write"):
            config["exchange_access"] = body["exchange_access"]
        exchange_access_changed = config.get("exchange_access", "read") != old_exchange_access

        agent.config = config
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()

        # Auto-restart running agents so new tokens / access mode are applied
        if (set(new_integrations) != old_integrations or msgraph_access_changed or exchange_access_changed) and agent.state == AgentState.RUNNING:
            await manager.restart_agent(agent_id)

        return {
            "agent_id": agent_id,
            "integrations": config["integrations"],
            "msgraph_access": config.get("msgraph_access", "read"),
            "exchange_access": config.get("exchange_access", "read"),
        }
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
    """Assign volume mounts to an agent (labels from admin catalog) and restart.

    Non-admin users may only assign mounts they have been granted access to via
    user_mount_access. Admins can assign any catalog mount.
    """
    await _check_owner(agent_id, user, db)
    from app.core.mounts import get_effective_catalog
    from app.models.user import UserRole
    from app.models.user_mount_access import UserMountAccess

    # Effective catalog = static env catalog + DB-managed Second Brains, so brains
    # created in the UI are assignable here too.
    catalog = await get_effective_catalog(db)
    new_mounts: list[str] = body.get("mounts", [])
    unknown = [m for m in new_mounts if m not in catalog]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown mount labels: {unknown}")

    # Non-admin: a mount is authorized if granted per-user OR via the user's
    # group/role (custom_role.permissions.mount_labels) — a UNION of both.
    effective_modes: dict[str, str] = {}
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from app.core.permissions import get_effective_permissions

        perms = await get_effective_permissions(user, db)
        role_mount_labels = set(perms.get("mount_labels") or [])

        grants = (await db.execute(
            select(UserMountAccess).where(UserMountAccess.user_id == user.id)
        )).scalars().all()
        grant_by_label = {g.mount_label: g.mode for g in grants}
        granted_labels = set(grant_by_label) | role_mount_labels
        denied = [m for m in new_mounts if m not in granted_labels]
        if denied:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized for mount(s): {denied}. Ask an admin to grant access via your group/role or in /admin → User → Mount Permissions.",
            )
        # Per-user grant caps the mode; a pure group grant uses the catalog default.
        effective_modes = {
            label: (
                ("ro" if "ro" in (catalog[label].mode, grant_by_label[label]) else "rw")
                if label in grant_by_label else catalog[label].mode
            )
            for label in new_mounts
        }
    else:
        requested_modes = body.get("mount_modes") if isinstance(body.get("mount_modes"), dict) else {}
        effective_modes = {
            label: ("ro" if "ro" in (catalog[label].mode, requested_modes.get(label)) else "rw")
            for label in new_mounts
        }
    try:
        agent = await manager._get_agent(agent_id)
        config = agent.config or {}
        old_mounts = set(config.get("mounts", []))
        config["mounts"] = new_mounts
        config["mount_modes"] = effective_modes
        agent.config = config
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await db.commit()

        # Auto-restart if mounts changed and agent is running
        if set(new_mounts) != old_mounts and agent.state == AgentState.RUNNING:
            await manager.restart_agent(agent_id)

        return {"agent_id": agent_id, "mounts": new_mounts, "mount_modes": effective_modes}
    except ValueError:
        raise HTTPException(status_code=404, detail="Agent not found")


# --- Proactive Mode ---

class ProactiveUpdate(BaseModel):
    enabled: bool = True
    interval_seconds: int = 3600
    prompt: str | None = None  # legacy/unused: base prompt always lives in code
    # Per-agent additions appended to the code base prompt at fire time.
    # None = leave unchanged (toggle/interval-only saves); "" = clear.
    custom_instructions: str | None = None


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

        from app.core.agent_manager import PROACTIVE_PROMPT
        return {
            "agent_id": agent_id,
            "proactive": proactive,
            "schedule": schedule_stats,
            "base_prompt": PROACTIVE_PROMPT,
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

        # Preserve existing custom instructions unless the caller explicitly sends
        # a new value — toggle/interval-only updates omit it and must not wipe it.
        existing_custom = (proactive or {}).get("custom_instructions", "")
        new_custom = (
            body.custom_instructions
            if body.custom_instructions is not None
            else existing_custom
        )

        if schedule_id:
            result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
            schedule = result.scalar_one_or_none()
            if schedule:
                schedule.enabled = body.enabled
                schedule.interval_seconds = body.interval_seconds
                if body.enabled:
                    schedule.next_run_at = now + timedelta(seconds=body.interval_seconds)
            else:
                schedule_id = None

        if not schedule_id:
            schedule_id = uuid.uuid4().hex[:8]
            schedule = Schedule(
                id=schedule_id,
                name=f"[Proactive] {agent.name}",
                # Base prompt always comes from code at fire time (scheduler);
                # this stored copy is only a placeholder for the schedule row.
                prompt=PROACTIVE_PROMPT,
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
            "custom_instructions": new_custom,
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
    manager: AgentManager = Depends(_get_agent_manager),
    agent_auth: dict = Depends(verify_agent_token),
):
    """Send a direct Telegram message to all authorized users of this agent.

    Called by the agent's MCP send_telegram tool. The caller must present a
    valid agent token (Authorization: Bearer + X-Agent-ID) whose agent_id
    matches the path — otherwise any unauthenticated caller could send Telegram
    messages in an agent's name.
    """
    if agent_auth["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="Agent token does not match target agent")
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
            if sent_to == 0:
                # Fallback for agents without their own Telegram bot: deliver through
                # any running per-agent bot that already has authorized chats.
                import redis.asyncio as aioredis
                redis = aioredis.from_url(settings.redis_url, decode_responses=True)
                try:
                    for fallback_agent_id, fallback_bot in list(getattr(tg_manager, "_bots", {}).items()):
                        if not fallback_bot or not fallback_bot._started:
                            continue
                        count = await redis.scard(f"agent:{fallback_agent_id}:tg_auth")
                        if count <= 0:
                            continue
                        for cid in await redis.smembers(f"agent:{fallback_agent_id}:tg_auth"):
                            await redis.setex(f"telegram:chat:{cid}:active_agent", 86400, agent_id)
                        prefix = ""
                        if fallback_agent_id != agent_id:
                            try:
                                agent = await manager._get_agent(agent_id)
                                prefix = f"*{agent.name}:*\n"
                            except Exception:
                                prefix = f"*Agent {agent_id}:*\n"
                        await fallback_bot.send_to_all_authorized(prefix + body.message)
                        sent_to = count
                        break
                finally:
                    await redis.aclose()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if sent_to == 0:
        raise HTTPException(
            status_code=503,
            detail="No running Telegram bot with authorized chats is available",
        )

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
