"""In-app Analytics API — skill time savings, agent performance, platform overview.

Powers the /analytics dashboard in the frontend. No Grafana required.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.agent import Agent, AgentState
from app.models.skill import Skill, SkillTaskUsage
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


def _days_ago(n: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


# ---------------------------------------------------------------------------
# Platform overview
# ---------------------------------------------------------------------------

@router.get("/overview")
async def get_overview(
    days: int = Query(30, ge=1, le=365),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide stats for the analytics dashboard header cards."""
    since = _days_ago(days)

    # Task stats
    task_result = await db.execute(
        select(
            func.count(Task.id).label("total"),
            func.sum(Task.cost_usd).label("total_cost"),
            func.avg(Task.duration_ms).label("avg_duration_ms"),
        ).where(Task.created_at >= since)
    )
    task_row = task_result.one()

    completed_result = await db.execute(
        select(func.count(Task.id)).where(
            Task.created_at >= since,
            Task.status == TaskStatus.COMPLETED,
        )
    )
    completed = completed_result.scalar() or 0
    total_tasks = task_row.total or 0
    success_rate = round(completed / total_tasks * 100, 1) if total_tasks else 0.0

    # Total time saved across all skill usages in the period
    savings_result = await db.execute(
        select(func.sum(SkillTaskUsage.time_saved_seconds)).where(
            SkillTaskUsage.created_at >= since,
            SkillTaskUsage.time_saved_seconds.isnot(None),
        )
    )
    total_time_saved_seconds = int(savings_result.scalar() or 0)

    # Active agents
    agents_result = await db.execute(
        select(func.count(Agent.id)).where(
            Agent.state.in_([AgentState.RUNNING, AgentState.IDLE])
        )
    )
    active_agents = agents_result.scalar() or 0

    # Avg task rating
    avg_rating_result = await db.execute(
        select(func.avg(TaskRating.rating)).where(TaskRating.created_at >= since)
    )
    avg_rating = avg_rating_result.scalar()

    # Daily task volume for sparkline (last `days` days)
    from sqlalchemy import text as sa_text
    daily_result = await db.execute(
        sa_text("""
            SELECT date_trunc('day', created_at) AS day,
                   COUNT(id) AS count,
                   COALESCE(SUM(cost_usd), 0) AS cost
            FROM tasks
            WHERE created_at >= :since
            GROUP BY date_trunc('day', created_at)
            ORDER BY date_trunc('day', created_at)
        """),
        {"since": since},
    )
    daily_rows = daily_result.all()
    daily_tasks = [{"date": str(r.day)[:10], "count": r.count, "cost": float(r.cost or 0)} for r in daily_rows]

    return {
        "period_days": days,
        "total_tasks": total_tasks,
        "completed_tasks": completed,
        "success_rate_pct": success_rate,
        "total_cost_usd": round(float(task_row.total_cost or 0), 4),
        "avg_duration_ms": int(task_row.avg_duration_ms or 0),
        "total_time_saved_seconds": total_time_saved_seconds,
        "active_agents": active_agents,
        "avg_task_rating": round(float(avg_rating), 2) if avg_rating else None,
        "daily_tasks": daily_tasks,
    }


# ---------------------------------------------------------------------------
# Skill analytics
# ---------------------------------------------------------------------------

@router.get("/skills")
async def get_skills_analytics(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Per-skill analytics: time savings vs manual, rating trend, usage stats."""
    since = _days_ago(days)

    # All active skills — show even with 0 usage so dashboard is never empty
    skills_result = await db.execute(
        select(Skill).where(Skill.status == "active").order_by(Skill.usage_count.desc(), Skill.name).limit(limit)
    )
    skills = list(skills_result.scalars().all())

    skill_ids = [s.id for s in skills]

    # Aggregate usage data for the period
    usage_agg = await db.execute(
        select(
            SkillTaskUsage.skill_id,
            func.count(SkillTaskUsage.id).label("period_uses"),
            func.avg(SkillTaskUsage.skill_helpfulness).label("avg_helpfulness"),
            func.avg(SkillTaskUsage.agent_self_rating).label("avg_agent_rating"),
            func.avg(SkillTaskUsage.user_rating).label("avg_user_rating"),
            func.sum(SkillTaskUsage.time_saved_seconds).label("total_time_saved"),
            func.avg(SkillTaskUsage.task_duration_ms).label("avg_agent_duration_ms"),
            func.sum(SkillTaskUsage.task_cost_usd).label("total_cost_usd"),
        )
        .where(
            SkillTaskUsage.skill_id.in_(skill_ids),
            SkillTaskUsage.created_at >= since,
        )
        .group_by(SkillTaskUsage.skill_id)
    )
    usage_by_skill = {row.skill_id: row for row in usage_agg.all()}

    result = []
    for skill in skills:
        u = usage_by_skill.get(skill.id)
        manual_secs = skill.manual_duration_seconds
        avg_agent_ms = float(u.avg_agent_duration_ms) if u and u.avg_agent_duration_ms else (
            skill.avg_agent_duration_ms
        )
        avg_agent_secs = (avg_agent_ms / 1000) if avg_agent_ms else None
        time_saved_per_use = (
            max(0, manual_secs - avg_agent_secs) if manual_secs and avg_agent_secs else None
        )
        roi_factor = (
            round(manual_secs / avg_agent_secs, 1) if manual_secs and avg_agent_secs and avg_agent_secs > 0 else None
        )

        result.append({
            "id": skill.id,
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
            "usage_count": skill.usage_count,
            "period_uses": u.period_uses if u else 0,
            "avg_rating": round(float(skill.avg_rating), 2) if skill.avg_rating else None,
            "avg_helpfulness": round(float(u.avg_helpfulness), 2) if u and u.avg_helpfulness else None,
            "avg_agent_self_rating": round(float(u.avg_agent_rating), 2) if u and u.avg_agent_rating else None,
            "avg_user_rating": round(float(u.avg_user_rating), 2) if u and u.avg_user_rating else None,
            # Time savings
            "manual_duration_seconds": manual_secs,
            "avg_agent_duration_seconds": round(avg_agent_secs, 1) if avg_agent_secs else None,
            "time_saved_per_use_seconds": round(time_saved_per_use) if time_saved_per_use else None,
            "total_time_saved_seconds": int(u.total_time_saved) if u and u.total_time_saved else 0,
            "roi_factor": roi_factor,
            # Cost
            "total_cost_usd": round(float(u.total_cost_usd), 4) if u and u.total_cost_usd else 0.0,
        })

    return {"period_days": days, "skills": result}


@router.get("/skills/{skill_id}/trend")
async def get_skill_trend(
    skill_id: int,
    days: int = Query(60, ge=7, le=365),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Weekly quality + time-savings trend for a single skill."""
    since = _days_ago(days)

    skill_result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = skill_result.scalar_one_or_none()
    if not skill:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Skill not found")

    from sqlalchemy import text as sa_text
    weekly = await db.execute(
        sa_text("""
            SELECT date_trunc('week', created_at) AS week,
                   COUNT(id) AS uses,
                   AVG(skill_helpfulness) AS avg_helpfulness,
                   AVG(user_rating) AS avg_user_rating,
                   AVG(agent_self_rating) AS avg_agent_rating,
                   SUM(time_saved_seconds) AS time_saved
            FROM skill_task_usages
            WHERE skill_id = :skill_id AND created_at >= :since
            GROUP BY date_trunc('week', created_at)
            ORDER BY date_trunc('week', created_at)
        """),
        {"skill_id": skill_id, "since": since},
    )

    trend = []
    for row in weekly.all():
        trend.append({
            "week": str(row.week)[:10],
            "uses": row.uses,
            "avg_helpfulness": round(float(row.avg_helpfulness), 2) if row.avg_helpfulness else None,
            "avg_user_rating": round(float(row.avg_user_rating), 2) if row.avg_user_rating else None,
            "avg_agent_rating": round(float(row.avg_agent_rating), 2) if row.avg_agent_rating else None,
            "time_saved_seconds": int(row.time_saved) if row.time_saved else 0,
        })

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "manual_duration_seconds": skill.manual_duration_seconds,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Agent analytics
# ---------------------------------------------------------------------------

@router.get("/agents")
async def get_agents_analytics(
    days: int = Query(30, ge=1, le=365),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Per-agent performance: task volume, success rate, cost, avg rating."""
    since = _days_ago(days)

    agents_result = await db.execute(select(Agent))
    agents = list(agents_result.scalars().all())

    agent_ids = [a.id for a in agents]

    task_agg = await db.execute(
        select(
            Task.agent_id,
            func.count(Task.id).label("total"),
            func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED).label("completed"),
            func.coalesce(func.sum(Task.cost_usd), 0).label("total_cost"),
            func.avg(Task.duration_ms).label("avg_duration_ms"),
        )
        .where(Task.agent_id.in_(agent_ids), Task.created_at >= since)
        .group_by(Task.agent_id)
    )
    task_by_agent = {row.agent_id: row for row in task_agg.all()}

    rating_agg = await db.execute(
        select(
            TaskRating.agent_id,
            func.avg(TaskRating.rating).label("avg_rating"),
            func.count(TaskRating.id).label("rating_count"),
        )
        .where(TaskRating.agent_id.in_(agent_ids), TaskRating.created_at >= since)
        .group_by(TaskRating.agent_id)
    )
    rating_by_agent = {row.agent_id: row for row in rating_agg.all()}

    result = []
    for agent in agents:
        t = task_by_agent.get(agent.id)
        r = rating_by_agent.get(agent.id)
        total = t.total if t else 0
        result.append({
            "id": agent.id,
            "name": agent.name,
            "state": agent.state,
            "role": agent.config.get("role") if agent.config else None,
            "total_tasks": total,
            "success_rate_pct": round(
                (t.completed or 0) / total * 100, 1
            ) if total else 0.0,
            "total_cost_usd": round(float(t.total_cost or 0), 4) if t else 0.0,
            "avg_duration_ms": int(t.avg_duration_ms or 0) if t else 0,
            "avg_rating": round(float(r.avg_rating), 2) if r and r.avg_rating else None,
            "rating_count": r.rating_count if r else 0,
        })

    result.sort(key=lambda x: x["total_tasks"], reverse=True)
    return {"period_days": days, "agents": result}


@router.get("/agents/{agent_id}")
async def get_agent_detail(
    agent_id: str,
    days: int = Query(30, ge=1, le=365),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Detailed analytics for a single agent: daily volume, recent ratings, top errors."""
    from sqlalchemy import text as sa_text
    since = _days_ago(days)

    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Task summary
    task_row = (await db.execute(
        select(
            func.count(Task.id).label("total"),
            func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED).label("completed"),
            func.count(Task.id).filter(Task.status == TaskStatus.FAILED).label("failed"),
            func.coalesce(func.sum(Task.cost_usd), 0).label("total_cost"),
            func.avg(Task.duration_ms).label("avg_duration_ms"),
            func.avg(Task.num_turns).label("avg_turns"),
        )
        .where(Task.agent_id == agent_id, Task.created_at >= since)
    )).one()

    # Daily volume
    daily = (await db.execute(
        sa_text("""
            SELECT date_trunc('day', created_at) AS day,
                   COUNT(id) AS total,
                   COUNT(id) FILTER (WHERE status = 'completed') AS completed,
                   COUNT(id) FILTER (WHERE status = 'failed') AS failed
            FROM tasks
            WHERE agent_id = :agent_id AND created_at >= :since
            GROUP BY date_trunc('day', created_at)
            ORDER BY date_trunc('day', created_at)
        """),
        {"agent_id": agent_id, "since": since},
    )).mappings().all()

    # Recent ratings with comments
    ratings_result = await db.execute(
        select(TaskRating)
        .where(TaskRating.agent_id == agent_id)
        .order_by(TaskRating.created_at.desc())
        .limit(20)
    )
    ratings = ratings_result.scalars().all()

    # Top error patterns (from failed task titles/errors)
    errors_result = await db.execute(
        select(Task.title, Task.error)
        .where(Task.agent_id == agent_id, Task.status == TaskStatus.FAILED, Task.created_at >= since)
        .order_by(Task.created_at.desc())
        .limit(10)
    )
    recent_errors = [{"title": r.title, "error": (r.error or "")[:200]} for r in errors_result.all()]

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "role": agent.config.get("role") if agent.config else None,
            "state": agent.state,
        },
        "period_days": days,
        "summary": {
            "total_tasks": task_row.total or 0,
            "completed": task_row.completed or 0,
            "failed": task_row.failed or 0,
            "success_rate_pct": round((task_row.completed or 0) / task_row.total * 100, 1) if task_row.total else 0.0,
            "total_cost_usd": round(float(task_row.total_cost or 0), 4),
            "avg_duration_ms": int(task_row.avg_duration_ms or 0),
            "avg_turns": round(float(task_row.avg_turns or 0), 1),
        },
        "daily": [
            {"date": str(r["day"])[:10], "total": r["total"], "completed": r["completed"], "failed": r["failed"]}
            for r in daily
        ],
        "ratings": [
            {
                "id": r.id,
                "rating": r.rating,
                "comment": r.comment,
                "task_id": r.task_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in ratings
        ],
        "recent_errors": recent_errors,
    }
