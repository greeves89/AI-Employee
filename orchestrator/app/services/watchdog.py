"""Watchdog detection helpers (issue #211).

Pure, dependency-light detection logic for stale tasks and missed schedules,
kept out of scheduler_service so it can be unit-tested without pulling in the
docker/redis import chain. The SchedulerService owns the loop, DB sessions and
alerting; this module owns the "is it stale / missed?" decision.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus

# A RUNNING task bumps updated_at (TimestampMixin onupdate) on every status/step
# write, so no bump for this long means the worker died silently. A schedule
# whose next_run_at slipped past the grace window means the scheduler was down
# during its fire time (container restart).
_STALE_TASK_THRESHOLD = timedelta(minutes=30)
_MISSED_SCHEDULE_GRACE = timedelta(minutes=5)


def md_escape(s: str) -> str:
    """Escape Telegram-Markdown metacharacters in a free-text value."""
    return (
        s.replace("\\", "\\\\")
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a possibly naive datetime to timezone-aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def is_task_stale(task: Task, now: datetime, threshold: timedelta = _STALE_TASK_THRESHOLD) -> bool:
    """A RUNNING task is stale when its last heartbeat (updated_at) is older than threshold."""
    if task.status != TaskStatus.RUNNING:
        return False
    updated = as_utc(task.updated_at)
    if updated is None:
        return False
    return (now - updated) > threshold


def is_schedule_missed(
    schedule: Schedule, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> bool:
    """An enabled schedule is missed when next_run_at slipped past the grace window."""
    if not schedule.enabled:
        return False
    nra = as_utc(schedule.next_run_at)
    if nra is None:
        return False
    return (now - nra) > grace


def mark_task_stale(task: Task, now: datetime) -> Task:
    """Flip a stale task to FAILED with a diagnostic error + metadata flag."""
    minutes = int(_STALE_TASK_THRESHOLD.total_seconds() // 60)
    task.status = TaskStatus.FAILED
    task.completed_at = now
    task.error = f"Watchdog: no heartbeat for over {minutes} min — task marked stale."
    meta = dict(task.metadata_ or {})
    meta["stale"] = True
    meta["stale_detected_at"] = now.isoformat()
    task.metadata_ = meta
    return task


async def find_stale_tasks(
    db, now: datetime, threshold: timedelta = _STALE_TASK_THRESHOLD
) -> list[Task]:
    """Return RUNNING tasks whose heartbeat is older than the threshold."""
    cutoff = now - threshold
    result = await db.execute(
        select(Task).where(Task.status == TaskStatus.RUNNING, Task.updated_at < cutoff)
    )
    return [t for t in result.scalars().all() if is_task_stale(t, now, threshold)]


async def find_missed_schedules(
    db, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> list[Schedule]:
    """Return enabled schedules whose next_run_at slipped past the grace window."""
    cutoff = now - grace
    result = await db.execute(
        select(Schedule).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.next_run_at < cutoff,
        )
    )
    return [s for s in result.scalars().all() if is_schedule_missed(s, now, grace)]
