"""Activity timeline — one day-strip per agent: planned runs (from schedules)
and actual runs (from tasks) over an arbitrary UTC time range, past or future.

Backs the "Activity" page (HANDOVER.md Schritt 2): the user wants to see what
each agent has planned for a day and what it actually did, with the same
date-navigation for the past as for the future.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ownership import visible_agent_ids
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.agent import Agent
from app.models.schedule import Schedule
from app.models.task import Task

router = APIRouter(prefix="/activity", tags=["activity"])

# The UI only ever requests single-day ranges. schedule_occurrences() enumerates
# fire times synchronously (no await) inside this async handler — an unbounded
# range times an unbounded number of schedules could stall the event loop for
# every concurrent request, not just the caller's. ~13 months covers any
# reasonable "compare to last year" use without allowing an arbitrary span.
_MAX_RANGE = timedelta(days=400)

# Matches _MAX_OCCURRENCES's spirit for the task side of the response — an
# extreme range shouldn't return every task an agent has ever run.
_MAX_TASKS = 2000


def _to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


@router.get("/timeline")
async def get_activity_timeline(
    start: datetime = Query(..., description="Range start, ISO 8601 (inclusive)"),
    end: datetime = Query(..., description="Range end, ISO 8601 (exclusive)"),
    agent_id: str | None = Query(None, description="Limit to a single agent"),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """One entry per visible agent: task bars + planned-run markers overlapping
    [start, end). Ownership-scoped identically to cost attribution/analytics."""
    from app.core.plan_rhythm import describe_schedule
    from app.services.scheduler_service import schedule_occurrences

    # Query-param datetimes with no offset are ambiguous — treat as UTC rather
    # than silently misinterpreting them however the DB driver happens to.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if end - start > _MAX_RANGE:
        raise HTTPException(status_code=422, detail=f"range too large — max {_MAX_RANGE.days} days")

    vids = await visible_agent_ids(user, db)
    if agent_id and vids is not None and agent_id not in vids:
        raise HTTPException(status_code=404, detail="Agent not found")
    if vids is not None and not vids:
        return {"start": _to_iso(start), "end": _to_iso(end), "agents": []}

    agents_query = select(Agent)
    if vids is not None:
        agents_query = agents_query.where(Agent.id.in_(vids))
    if agent_id:
        agents_query = agents_query.where(Agent.id == agent_id)
    agents_query = agents_query.order_by(Agent.name)
    agents = (await db.execute(agents_query)).scalars().all()
    if agent_id and not agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agents:
        return {"start": _to_iso(start), "end": _to_iso(end), "agents": []}

    agent_ids = [a.id for a in agents]

    # A task "overlaps" the range if it started before the range ends, and
    # either finished at/after the range starts or hasn't finished yet (still
    # running — rendered as an in-progress bar extending to "now" client-side).
    tasks_query = (
        select(Task)
        .where(
            Task.agent_id.in_(agent_ids),
            Task.started_at.isnot(None),
            Task.started_at < end,
            or_(Task.completed_at >= start, Task.completed_at.is_(None)),
        )
        .order_by(Task.started_at)
        .limit(_MAX_TASKS)
    )
    tasks = (await db.execute(tasks_query)).scalars().all()

    schedules_query = select(Schedule).where(
        Schedule.agent_id.in_(agent_ids), Schedule.enabled == True  # noqa: E712
    )
    schedules = (await db.execute(schedules_query)).scalars().all()

    tasks_by_agent: dict[str, list[Task]] = {aid: [] for aid in agent_ids}
    for t in tasks:
        tasks_by_agent.setdefault(t.agent_id, []).append(t)

    schedules_by_agent: dict[str, list[Schedule]] = {aid: [] for aid in agent_ids}
    for s in schedules:
        schedules_by_agent.setdefault(s.agent_id, []).append(s)

    result_agents = []
    for a in agents:
        marks = []
        for s in schedules_by_agent.get(a.id, []):
            # Takt und Art gehoeren mit: sonst steht im Kalender nur eine Uhrzeit, und
            # ob dahinter ein taeglicher Rhythmus oder ein Einmal-Lauf steckt, sieht
            # man erst, wenn der Agent es zufaellig in den Namen geschrieben hat.
            rhythm = describe_schedule(s)
            kind = (
                "plan" if s.name.startswith("[Plan] ")
                else "rhythm" if s.name.startswith("[Rhythmus] ")
                else "proactive" if s.name.startswith("[Proactive]")
                else "meeting" if s.prompt.startswith("__meeting__:")
                else "custom"
            )
            for occ in schedule_occurrences(s, start, end):
                marks.append({
                    "time": _to_iso(occ),
                    "schedule_id": s.id,
                    "schedule_name": s.name,
                    "rhythm": rhythm,
                    "kind": kind,
                })
        marks.sort(key=lambda m: m["time"])

        bars = [
            {
                "task_id": t.id,
                "title": t.title,
                "status": t.status.value if hasattr(t.status, "value") else t.status,
                "started_at": _to_iso(t.started_at),
                "completed_at": _to_iso(t.completed_at),
                "duration_ms": t.duration_ms,
                "cost_usd": t.cost_usd,
            }
            for t in sorted(tasks_by_agent.get(a.id, []), key=lambda t: t.started_at)
        ]

        result_agents.append({
            "agent_id": a.id,
            "name": a.name,
            "tasks": bars,
            "scheduled_marks": marks,
        })

    return {"start": _to_iso(start), "end": _to_iso(end), "agents": result_agents}
