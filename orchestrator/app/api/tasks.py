from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.load_balancer import LoadBalancer
from app.core.pricing import estimate_prompt_cost
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import get_redis_service, is_agent_principal, require_auth, require_auth_or_agent
from app.models.task import Task, TaskStatus
from app.schemas.task import TaskBatchCreate, TaskBatchResponse, TaskCreate, TaskListResponse, TaskResponse
from app.services.redis_service import RedisService

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Dry-Run / Simulation (#386): the agent produces a PLAN instead of executing, so
# the user can preview (and approve) what would happen before anything is done.
_DRY_RUN_WRAPPER = """[DRY-RUN / SIMULATION — NICHT AUSFÜHREN]
Führe die unten stehende Aufgabe NICHT aus. Erstelle stattdessen einen klaren, strukturierten Ausführungsplan als Vorschau für den Nutzer:

1. **Schritte:** Welche Schritte würdest du in welcher Reihenfolge gehen?
2. **Auswirkungen:** Welche Dateien/Befehle/Ressourcen wären betroffen (konkrete Pfade/Kommandos)?
3. **Externe Aktionen:** Welche Nachrichten/Mails/API-Calls würdest du an wen senden?
4. **Aufwand:** grobe Zeit-/Kostenschätzung und mögliche Risiken.

WICHTIG: Ändere nichts, sende nichts, führe keine Tools mit Nebenwirkungen aus. Gib NUR den Plan als Antwort zurück.

--- AUFGABE ---
{task}"""


def _get_task_router(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> TaskRouter:
    lb = LoadBalancer(redis)
    docker = getattr(request.app.state, "docker", None)
    return TaskRouter(db, redis, lb, docker_service=docker)


def _agent_delegated_this(user, task) -> bool:
    """Darf dieser Agent die Aufgabe sehen, weil ER sie vergeben hat?

    Ein Agent sieht sonst ausschliesslich seine EIGENEN Aufgaben — ``[user.id]``.
    Fuer einen Team-Lead heisst das: er legt per ``delegate_and_wait`` Auftraege
    fuer seine Leute an und darf danach kein einziges Ergebnis abrufen. Genau das
    stand am 2026-08-12 beim Kunden im Chat: vier Auftraege, alle vier in der
    Datenbank auf COMPLETED mit 4-10 Zuegen echter Arbeit — und der Lead meldete
    „Der anschliessende Statusabruf liefert fuer alle vier Auftraege derzeit
    'nicht abrufbar'. Das ist kein belastbarer Abschluss."

    Der Abruf lief auf 403. Die Arbeit war getan, sie kam nur nie zurueck.

    Die Mandantentrennung bleibt: Zugriff hat, wer die Aufgabe **erzeugt** hat —
    nicht jeder Agent, und nicht agentenuebergreifend.
    """
    return bool(
        is_agent_principal(user)
        and (getattr(task, "metadata_", None) or {}).get("created_by_agent") == user.id
    )


async def _get_user_agent_ids(user, db: AsyncSession) -> list[str] | None:
    """Return agent IDs owned by user, or None if admin (sees all).

    Ownerless agents (user_id IS NULL) are NOT auto-included here (changed
    2026-08-27) — they used to count as "platform agents" visible to
    everyone, which on a multi-department customer install meant
    every user saw every other department's tasks/costs the moment an
    agent was created without an assigned owner (usually by accident, not
    decision). ``is_platform_agent`` is the explicit, admin-set flag for a
    DELIBERATELY shared agent — that one still counts as visible to all.
    """
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return None
    if is_agent_principal(user):
        return [user.id]
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess
    owned = await db.execute(
        select(Agent.id).where(
            (Agent.user_id == user.id) | (Agent.is_platform_agent.is_(True))
        )
    )
    shared = await db.execute(
        select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
    )
    return list({row[0] for row in owned.all()} | {row[0] for row in shared.all()})


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    status: TaskStatus | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    lite: bool = Query(default=False),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    router_: TaskRouter = Depends(_get_task_router),
):
    agent_ids = await _get_user_agent_ids(user, db) if hasattr(user, "role") else None
    tasks = await router_.list_tasks(
        status=status,
        agent_id=agent_id,
        agent_ids=agent_ids,
        limit=limit,
        offset=offset,
    )
    responses = [TaskResponse.model_validate(t) for t in tasks]
    if lite:
        for task in responses:
            task.prompt = task.prompt[:240]
            task.result = None
            task.error = None
    # Real total (not page size) — mirrors TaskRouter.list_tasks filter semantics,
    # otherwise the UI counter sticks at the page limit (e.g. "All 100").
    count_q = select(func.count(Task.id))
    if status:
        count_q = count_q.where(Task.status == status)
    if agent_id:
        count_q = count_q.where(Task.agent_id == agent_id)
    elif agent_ids is not None:
        count_q = count_q.where(Task.agent_id.in_(agent_ids))
    total = (await db.execute(count_q)).scalar() or 0
    return TaskListResponse(tasks=responses, total=int(total))


@router.post("/", response_model=TaskResponse, status_code=201)
async def create_task(
    data: TaskCreate,
    user=Depends(require_auth_or_agent),
    router_: TaskRouter = Depends(_get_task_router),
):
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewers cannot create tasks")

    prompt = data.prompt
    metadata: dict = {}
    if data.dry_run:
        # Wrap the prompt so the agent only plans; keep the original for "execute for real".
        metadata["dry_run"] = True
        metadata["original_prompt"] = data.prompt
        prompt = _DRY_RUN_WRAPPER.format(task=data.prompt)

    task = await router_.create_and_route_task(
        title=(f"[Vorschau] {data.title}" if data.dry_run else data.title),
        prompt=prompt,
        priority=data.priority,
        agent_id=data.agent_id,
        model=data.model,
        parent_task_id=data.parent_task_id,
        created_by_agent=data.created_by_agent,
        metadata={**metadata, "chat_session_id": data.chat_session_id}
        if data.chat_session_id else (metadata or None),
    )
    return TaskResponse.model_validate(task)


@router.post("/batch", response_model=TaskBatchResponse, status_code=201)
async def create_task_batch(
    data: TaskBatchCreate,
    user=Depends(require_auth_or_agent),
    router_: TaskRouter = Depends(_get_task_router),
):
    """Create multiple tasks in a single call for parallel sub-agent execution.

    All tasks are created independently and can run on different agents
    simultaneously. If parent_task_id is set, all tasks become subtasks
    of that parent. The parent agent is notified individually as each
    subtask completes (not aggregated).
    """
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewers cannot create tasks")

    if not data.tasks:
        raise HTTPException(status_code=400, detail="At least one task is required")
    if len(data.tasks) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 tasks per batch")

    created = []
    for task_data in data.tasks:
        task = await router_.create_and_route_task(
            title=task_data.title,
            prompt=task_data.prompt,
            priority=task_data.priority,
            agent_id=task_data.agent_id,
            model=task_data.model,
            parent_task_id=data.parent_task_id or task_data.parent_task_id,
            created_by_agent=data.created_by_agent or task_data.created_by_agent,
            metadata=({"chat_session_id": task_data.chat_session_id}
                      if task_data.chat_session_id else None),
        )
        created.append(TaskResponse.model_validate(task))

    return TaskBatchResponse(
        tasks=created,
        total=len(created),
        parent_task_id=data.parent_task_id,
    )


class TaskEstimateRequest(BaseModel):
    prompt: str
    model: str | None = None
    agent_id: str | None = None


class TaskEstimateResponse(BaseModel):
    estimated_input_tokens: int
    model: str
    min_usd: float
    avg_usd: float
    max_usd: float
    agent_avg_usd: float | None = None  # Historical average for this agent


@router.post("/estimate", response_model=TaskEstimateResponse)
async def estimate_task_cost(
    data: TaskEstimateRequest,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Estimate the cost of a task before execution.

    Combines model pricing with historical agent performance data.
    """
    model = data.model or settings.default_model
    estimate = estimate_prompt_cost(data.prompt, model)

    # If agent specified, get historical average cost
    agent_avg = None
    if data.agent_id:
        from app.models.agent import Agent

        result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
        agent = result.scalar_one_or_none()
        if agent and agent.config:
            metrics = agent.config.get("metrics", {})
            total_cost = agent.config.get("total_cost_usd", 0)
            total_tasks = metrics.get("total", 0)
            if total_tasks > 0:
                agent_avg = round(total_cost / total_tasks, 6)

    return TaskEstimateResponse(
        **estimate,
        agent_avg_usd=agent_avg,
    )


class TaskSummaryResponse(BaseModel):
    active: int
    completed: int
    failed: int
    cancelled: int
    total: int
    total_cost_usd: float


@router.get("/summary", response_model=TaskSummaryResponse)
async def get_task_summary(
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Compact aggregate task stats for mobile dashboards."""
    agent_ids = await _get_user_agent_ids(user, db) if hasattr(user, "role") else None
    query = select(
        Task.status,
        func.count(Task.id).label("count"),
        func.coalesce(func.sum(Task.cost_usd), 0).label("cost"),
    ).group_by(Task.status)
    if agent_ids is not None:
        query = query.where(Task.agent_id.in_(agent_ids))

    result = await db.execute(query)
    counts = {status: 0 for status in TaskStatus}
    total_cost = 0.0
    for row in result.all():
        counts[row.status] = int(row.count or 0)
        total_cost += float(row.cost or 0)

    active = counts[TaskStatus.PENDING] + counts[TaskStatus.QUEUED] + counts[TaskStatus.RUNNING]
    completed = counts[TaskStatus.COMPLETED]
    failed = counts[TaskStatus.FAILED]
    cancelled = counts[TaskStatus.CANCELLED]
    return TaskSummaryResponse(
        active=active,
        completed=completed,
        failed=failed,
        cancelled=cancelled,
        total=sum(counts.values()),
        total_cost_usd=round(total_cost, 4),
    )


class AgentCostEntry(BaseModel):
    agent_id: str
    agent_name: str
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    task_count: int


class CostAttributionResponse(BaseModel):
    top_agents: list[AgentCostEntry]
    platform_total_usd: float
    platform_total_input_tokens: int
    platform_total_output_tokens: int


@router.get("/cost-attribution", response_model=CostAttributionResponse)
async def get_cost_attribution(
    limit: int = 5,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Top N agents by total cost with token breakdowns — scoped to the caller's own
    agents (admins see the whole platform). Non-admins must not see other tenants' cost."""
    from app.models.agent import Agent
    from app.core.ownership import visible_agent_ids

    vids = await visible_agent_ids(user, db)
    aids = list(vids) if vids is not None else None
    if aids is not None and not aids:
        # Fresh user with no agents → nothing to attribute.
        return CostAttributionResponse(
            top_agents=[], platform_total_usd=0.0,
            platform_total_input_tokens=0, platform_total_output_tokens=0,
        )

    top_where = [Task.agent_id.isnot(None), Task.cost_usd.isnot(None)]
    if aids is not None:
        top_where.append(Task.agent_id.in_(aids))
    result = await db.execute(
        select(
            Task.agent_id,
            func.sum(Task.cost_usd).label("total_cost"),
            func.sum(Task.input_tokens).label("total_input"),
            func.sum(Task.output_tokens).label("total_output"),
            func.count(Task.id).label("task_count"),
        )
        .where(*top_where)
        .group_by(Task.agent_id)
        .order_by(func.sum(Task.cost_usd).desc())
        .limit(limit)
    )
    rows = result.all()

    agent_ids = [r.agent_id for r in rows]
    agents_result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    agents_map = {a.id: a.name for a in agents_result.scalars().all()}

    top_agents = [
        AgentCostEntry(
            agent_id=r.agent_id,
            agent_name=agents_map.get(r.agent_id, "Unknown"),
            total_cost_usd=round(r.total_cost or 0, 4),
            total_input_tokens=r.total_input or 0,
            total_output_tokens=r.total_output or 0,
            task_count=r.task_count,
        )
        for r in rows
    ]

    totals_where = [Task.cost_usd.isnot(None)]
    if aids is not None:
        totals_where.append(Task.agent_id.in_(aids))
    totals = await db.execute(
        select(
            func.coalesce(func.sum(Task.cost_usd), 0).label("total_cost"),
            func.coalesce(func.sum(Task.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(Task.output_tokens), 0).label("total_output"),
        ).where(*totals_where)
    )
    t = totals.one()

    return CostAttributionResponse(
        top_agents=top_agents,
        platform_total_usd=round(float(t.total_cost), 4),
        platform_total_input_tokens=int(t.total_input),
        platform_total_output_tokens=int(t.total_output),
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    router_: TaskRouter = Depends(_get_task_router),
):
    task = await router_.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if hasattr(user, "role") and not _agent_delegated_this(user, task):
        allowed = await _get_user_agent_ids(user, db)
        if allowed is not None and task.agent_id not in allowed:
            raise HTTPException(status_code=403, detail="Access denied")
    return TaskResponse.model_validate(task)


@router.get("/{task_id}/steps")
async def get_task_steps(
    task_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return the persisted per-step execution history of a task (time-travel replay)."""
    from app.models.task_step import TaskStep

    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if hasattr(user, "role") and not _agent_delegated_this(user, task):
        allowed = await _get_user_agent_ids(user, db)
        if allowed is not None and task.agent_id not in allowed:
            raise HTTPException(status_code=403, detail="Access denied")

    steps = (await db.execute(
        select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.sequence.asc())
    )).scalars().all()
    return {
        "task_id": task_id,
        "total_steps": len(steps),
        "steps": [
            {
                "sequence": s.sequence,
                "type": s.event_type,
                "data": s.event_data,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            }
            for s in steps
        ],
    }


async def _assert_task_access(task_id: str, user, db: AsyncSession) -> Task:
    """Load a task and enforce ownership (mirrors get_task_steps). 404/403 on failure."""
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if hasattr(user, "role"):
        allowed = await _get_user_agent_ids(user, db)
        if allowed is not None and task.agent_id not in allowed:
            raise HTTPException(status_code=403, detail="Access denied")
    return task


@router.get("/{task_id}/trace")
async def get_task_trace(
    task_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Decision-Trace (issue #387): the enriched, grouped time-travel timeline for a
    task — thought -> tool call (input) -> matching result (output), per-step
    duration, governance audit events and a cost summary."""
    from app.services.trace_service import assemble_trace

    await _assert_task_access(task_id, user, db)
    trace = await assemble_trace(task_id, db)
    if trace is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return trace


@router.get("/{task_id}/export")
async def export_task_trace(
    task_id: str,
    format: str = Query("json", pattern="^(json)$"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Download the decision-trace as a file. JSON is served here; PDF is produced
    client-side via the browser's print-to-PDF on the trace view (no server dep)."""
    import json

    from fastapi.responses import Response

    from app.services.trace_service import assemble_trace

    await _assert_task_access(task_id, user, db)
    trace = await assemble_trace(task_id, db)
    if trace is None:
        raise HTTPException(status_code=404, detail="Task not found")
    body = json.dumps(trace, indent=2, ensure_ascii=False)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-trace.json"'},
    )


@router.post("/{task_id}/execute", response_model=TaskResponse, status_code=201)
async def execute_dry_run(
    task_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    router_: TaskRouter = Depends(_get_task_router),
):
    """Dry-Run (#386): take a plan-preview task and run it for real — same agent,
    the original (unwrapped) prompt, no dry-run. Returns the new task."""
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewers cannot create tasks")
    task = await _assert_task_access(task_id, user, db)
    if not task.dry_run or not task.original_prompt:
        raise HTTPException(status_code=400, detail="Task is not a dry-run preview")
    real = await router_.create_and_route_task(
        title=task.title.replace("[Vorschau] ", "", 1),
        prompt=task.original_prompt,
        priority=task.priority,
        agent_id=task.agent_id,
        model=task.model,
        metadata={"executed_from_dry_run": task_id},
    )
    return TaskResponse.model_validate(real)


@router.get("/{task_id}/artifacts")
async def get_task_artifacts(
    task_id: str,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List deliverables the agent produced for this task.

    Agents drop finished output into `/workspace/transfer/`. We list that dir on the
    task's agent and keep files whose mtime falls inside the task's run window
    (started_at .. completed_at + grace), so the user sees exactly what this task
    created — clickable, without digging through the file explorer. Download reuses
    the existing `/agents/{id}/files/download` endpoint (same AuthZ)."""
    from app.models.agent import Agent

    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    allowed = await _get_user_agent_ids(user, db)
    if allowed is not None and task.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Access denied")
    if not task.agent_id:
        return {"task_id": task_id, "agent_id": None, "artifacts": []}

    agent = (await db.execute(select(Agent).where(Agent.id == task.agent_id))).scalar_one_or_none()
    if not agent or not getattr(agent, "container_id", None):
        return {"task_id": task_id, "agent_id": task.agent_id, "artifacts": []}

    docker = getattr(request.app.state, "docker", None)
    if not docker:
        return {"task_id": task_id, "agent_id": task.agent_id, "artifacts": []}

    # Time window: files touched from just before the task started until a grace
    # period after completion (agents sometimes flush files right after finishing).
    start_ts = task.started_at.timestamp() - 30 if task.started_at else 0
    end_ts = (task.completed_at.timestamp() + 300) if task.completed_at else None

    artifacts: list[dict] = []
    try:
        exit_code, output = docker.exec_in_container(
            agent.container_id,
            ["find", "/workspace/transfer", "-type", "f",
             "-not", "-type", "l", "-printf", "%s|%T@|%p\n"],
        )
        for line in (output or "").strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            size_s, mtime_s, path = parts
            try:
                mtime = float(mtime_s)
            except ValueError:
                continue
            if mtime < start_ts:
                continue
            if end_ts is not None and mtime > end_ts:
                continue
            artifacts.append({
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "size": int(size_s) if size_s.isdigit() else 0,
                "modified": mtime,
            })
    except Exception:  # noqa: BLE001 — best-effort; empty list is a fine fallback
        return {"task_id": task_id, "agent_id": task.agent_id, "artifacts": []}

    artifacts.sort(key=lambda a: a["modified"], reverse=True)
    return {"task_id": task_id, "agent_id": task.agent_id, "artifacts": artifacts}


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    user=Depends(require_auth),
    router_: TaskRouter = Depends(_get_task_router),
):
    try:
        deleted = await router_.delete_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    user=Depends(require_auth),
    router_: TaskRouter = Depends(_get_task_router),
):
    try:
        task = await router_.cancel_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.post("/{task_id}/retain")
async def retain_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_auth),
):
    """Pin a task so the GC never auto-evicts it (UI is viewing it)."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.retain = True
    task.evict_after = None  # Cancel any scheduled eviction
    await db.commit()
    return {"ok": True, "task_id": task_id, "retain": True}


@router.post("/{task_id}/release")
async def release_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_auth),
):
    """Release a task so the GC can evict it after the grace period."""
    from datetime import datetime, timedelta, timezone
    from app.core.task_router import TASK_EVICT_GRACE_SECONDS
    from app.models.task import is_terminal_task_status

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.retain = False
    if is_terminal_task_status(task.status) and task.notified:
        task.evict_after = datetime.now(timezone.utc) + timedelta(seconds=TASK_EVICT_GRACE_SECONDS)
    await db.commit()
    return {"ok": True, "task_id": task_id, "retain": False}


# NOTE: kept above /{task_id} on purpose — a static route must be registered
# before the parametrized one, else "cost-attribution" is matched as a task_id.
