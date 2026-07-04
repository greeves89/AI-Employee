"""Shared agent-settings mutations (model/provider + autonomy level).

Single source of truth used by BOTH the HTTP API (`agents.py`) and the realtime
voice tools (`realtime_voice_session.py`), so authorization — ownership +
provider permission + model↔harness compatibility — can never be bypassed by a
caller. Every mutation runs the same checks here.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentState


async def change_agent_model(
    db: AsyncSession, user, agent_id: str, model: str, model_provider: str, manager=None
) -> dict:
    """Change an agent's model (within its current harness). Enforces ownership,
    the caller's provider permission, and model↔harness compatibility.

    ``manager`` is optional: when given, the container is restarted to pick up the
    new model immediately; when None (e.g. the voice path), the change applies to
    the DB and takes effect on the next container start/task."""
    from app.dependencies import require_agent_access
    from app.core.permissions import get_effective_permissions, can_use_llm_provider
    from app.core.model_catalog import is_model_allowed_for_mode, default_model_for_mode

    await require_agent_access(agent_id, user, db)  # ownership / access (403)
    perms = await get_effective_permissions(user, db)
    if not can_use_llm_provider(perms, model_provider):
        raise HTTPException(
            status_code=403,
            detail=f"Model-Provider '{model_provider}' ist für deine Rolle nicht erlaubt.",
        )
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not is_model_allowed_for_mode(agent.mode, model):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Modell '{model}' passt nicht zum Provider '{agent.mode}'. "
                f"Erlaubt sind z. B. '{default_model_for_mode(agent.mode)}'."
            ),
        )
    previous_model = agent.model
    agent.model = model
    config = dict(agent.config or {})
    config["model_provider"] = model_provider
    agent.config = config
    await db.commit()
    await db.refresh(agent)

    # Audit trail (parity with autonomy changes) — model is a security-relevant
    # setting and now also voice-triggerable.
    from app.models.audit_log import AuditLog, AuditEventType
    db.add(AuditLog(
        agent_id=agent_id,
        event_type=AuditEventType.AGENT_MODEL_CHANGED,
        command=f"model: {previous_model} → {model} ({model_provider})",
        outcome="success",
        user_id=str(getattr(user, "id", "")),
        meta={"previous_model": previous_model, "new_model": model, "model_provider": model_provider},
    ))
    await db.commit()

    if manager is not None and agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
        try:
            await manager.restart_agent(agent_id)
        except Exception:  # noqa: BLE001
            pass  # non-critical — next task uses the new model
    return {"agent_id": agent_id, "model": agent.model, "model_provider": model_provider}


PARALLEL_SESSIONS_MAX = 16  # sane ceiling — the agent container has a 2-CPU quota


async def change_parallel_sessions(
    db: AsyncSession, user, agent_id: str, sessions: int, manager=None
) -> dict:
    """Set how many sessions an agent runs in parallel. Applies equally to
    independent TASKS (proactive/scheduled) and CHATS (user conversations);
    anything beyond the limit queues in Redis and starts as a slot frees up.

    Stored in ``agent.config['parallel_sessions']`` and injected as both
    ``MAX_PARALLEL_TASKS`` and ``MAX_PARALLEL_CHATS`` when the container is
    (re)created. ``manager`` given → restart now so the new limit takes effect;
    None → applies on the next container start."""
    from app.dependencies import require_agent_access

    try:
        n = int(sessions)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="parallel_sessions must be an integer")
    if n < 1 or n > PARALLEL_SESSIONS_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"parallel_sessions must be between 1 and {PARALLEL_SESSIONS_MAX}",
        )
    await require_agent_access(agent_id, user, db)  # ownership / access (403)
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    config = dict(agent.config or {})
    config["parallel_sessions"] = n
    agent.config = config
    await db.commit()
    await db.refresh(agent)

    if manager is not None and agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
        try:
            await manager.restart_agent(agent_id)
        except Exception:  # noqa: BLE001
            pass  # non-critical — next container start uses the new limit
    return {"agent_id": agent_id, "parallel_sessions": n}


async def change_autonomy_level(db: AsyncSession, user, agent_id: str, level: str) -> dict:
    """Change an agent's autonomy level (l1–l4). Enforces ownership, fills the
    autonomy matrix + approval preset, and writes an audit log entry."""
    from app.dependencies import require_agent_access

    level = (level or "").lower()
    if level not in {"l1", "l2", "l3", "l4"}:
        raise HTTPException(status_code=422, detail="Invalid level. Must be l1, l2, l3, or l4")
    await require_agent_access(agent_id, user, db)  # ownership / access (403)
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    previous_level = agent.autonomy_level
    agent.autonomy_level = level
    from app.core import autonomy_matrix as am
    agent.config = {**(agent.config or {}), "autonomy_matrix": am.matrix_for_level(level)}
    await db.commit()

    from app.api.approval_rules import apply_autonomy_preset
    rules = await apply_autonomy_preset(db, agent_id, level)

    from app.models.audit_log import AuditLog, AuditEventType
    db.add(AuditLog(
        agent_id=agent_id,
        event_type=AuditEventType.AUTONOMY_LEVEL_CHANGED,
        command=f"autonomy_level: {previous_level} → {level}",
        outcome="success",
        user_id=str(user.id),
        meta={"previous_level": previous_level, "new_level": level,
              "rules_applied": [r.name for r in rules]},
    ))
    await db.commit()
    return {
        "agent_id": agent_id,
        "autonomy_level": level,
        "rules_applied": len(rules),
        "rule_names": [r.name for r in rules],
    }
