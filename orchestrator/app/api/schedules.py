import os
import uuid
from datetime import datetime, timedelta, timezone

from app.services.scheduler_service import _calc_next_run

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, require_auth_or_agent
from app.models.schedule import Schedule
from app.schemas.schedule import (
    ScheduleCreate,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdate,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _get_user_agent_ids(user, db: AsyncSession) -> list[str] | None:
    """Return agent IDs owned by user, or None if admin (sees all)."""
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return None
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess
    owned = await db.execute(
        select(Agent.id).where(
            (Agent.user_id == user.id) | (Agent.user_id.is_(None))
        )
    )
    shared = await db.execute(
        select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
    )
    return list({row[0] for row in owned.all()} | {row[0] for row in shared.all()})


async def _get_schedule(db: AsyncSession, schedule_id: str) -> Schedule:
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


async def _check_schedule_access(schedule: Schedule, user, db: AsyncSession) -> None:
    """Raise 403 if the calling user does not own the schedule's agent."""
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return
    allowed = await _get_user_agent_ids(user, db)
    if allowed is not None and schedule.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/", response_model=ScheduleListResponse)
async def list_schedules(user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    query = select(Schedule).order_by(Schedule.created_at.desc())
    if hasattr(user, "role"):
        allowed = await _get_user_agent_ids(user, db)
        if allowed is not None:
            query = query.where(Schedule.agent_id.in_(allowed))
    result = await db.execute(query)
    schedules = list(result.scalars().all())
    return ScheduleListResponse(
        schedules=[ScheduleResponse.from_schedule(s) for s in schedules],
        total=len(schedules),
    )


@router.post("/", response_model=ScheduleResponse, status_code=201)
async def create_schedule(data: ScheduleCreate, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    schedule = Schedule(
        id=schedule_id,
        name=data.name,
        prompt=data.prompt,
        interval_seconds=data.interval_seconds,
        cron_expression=data.cron_expression,
        priority=data.priority,
        agent_id=data.agent_id,
        model=data.model,
        enabled=True,
        next_run_at=_calc_next_run(data, now),  # type: ignore[arg-type]
        total_runs=0,
        success_count=0,
        fail_count=0,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return ScheduleResponse.from_schedule(schedule)


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    return ScheduleResponse.from_schedule(schedule)


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str, data: ScheduleUpdate, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db),
):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)

    # Recalculate next_run if timing changed
    if data.interval_seconds is not None or data.cron_expression is not None:
        now = datetime.now(timezone.utc)
        schedule.next_run_at = _calc_next_run(schedule, now)

    await db.commit()
    await db.refresh(schedule)
    return ScheduleResponse.from_schedule(schedule)


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    await db.delete(schedule)
    await db.commit()
    return {"status": "deleted", "schedule_id": schedule_id}


@router.post("/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    """Manually trigger a schedule to run immediately."""
    from app.core.agent_manager import PROACTIVE_PROMPT
    from app.core.load_balancer import LoadBalancer
    from app.core.task_router import TaskRouter
    from app.services.redis_service import RedisService

    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)

    redis = RedisService(os.environ.get("REDIS_URL", "redis://redis:6379"))
    await redis.connect()
    try:
        lb = LoadBalancer(redis)
        router = TaskRouter(db, redis, lb)

        is_proactive = schedule.name.startswith("[Proactive]")
        prompt = PROACTIVE_PROMPT if is_proactive else schedule.prompt

        task = await router.create_and_route_task(
            title=f"[Manual] {schedule.name}",
            prompt=prompt,
            priority=schedule.priority,
            agent_id=schedule.agent_id,
            model=schedule.model,
        )

        now = datetime.now(timezone.utc)
        schedule.last_run_at = now
        schedule.total_runs += 1
        await db.commit()

        return {"status": "triggered", "schedule_id": schedule_id, "task_id": task.id}
    finally:
        await redis.disconnect()


@router.post("/{schedule_id}/pause")
async def pause_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    schedule.enabled = False
    await db.commit()
    return {"status": "paused", "schedule_id": schedule_id}


@router.post("/{schedule_id}/resume")
async def resume_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    schedule.enabled = True
    now = datetime.now(timezone.utc)
    schedule.next_run_at = _calc_next_run(schedule, now)
    await db.commit()
    return {"status": "resumed", "schedule_id": schedule_id}
