"""In-app Analytics API — skill time savings, agent performance, platform overview.

Powers the /analytics dashboard in the frontend. No Grafana required.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log
from app.core.ownership import visible_agent_ids
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.agent import Agent, AgentState
from app.models.chat_message import ChatMessage
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
    """Per-user stats for the analytics dashboard header cards.

    Scoped to the caller's own agents (admins see the whole platform). A non-admin
    must never see another tenant's tasks, cost or agents here.
    """
    since = _days_ago(days)

    # Multi-tenant scope: None = admin (all), else the caller's own/shared agents.
    vids = await visible_agent_ids(user, db)
    if vids is not None and not vids:
        # Fresh user with no agents → everything is zero (never fall through to global).
        return {
            "period_days": days, "total_tasks": 0, "completed_tasks": 0,
            "success_rate_pct": 0.0, "total_cost_usd": 0.0, "total_task_cost_usd": 0.0,
            "total_chat_cost_usd": 0.0, "avg_duration_ms": 0, "total_time_saved_seconds": 0,
            "active_agents": 0, "avg_task_rating": None, "daily_tasks": [],
        }
    aids = list(vids) if vids is not None else None

    def _scope_task(stmt):
        return stmt.where(Task.agent_id.in_(aids)) if aids is not None else stmt

    # Task stats
    task_result = await db.execute(
        _scope_task(select(
            func.count(Task.id).label("total"),
            func.sum(Task.cost_usd).label("total_cost"),
            func.avg(Task.duration_ms).label("avg_duration_ms"),
        ).where(Task.created_at >= since))
    )
    task_row = task_result.one()

    # Chat cost stats — assistant chat turns are billed too
    chat_stmt = select(func.sum(ChatMessage.cost_usd)).where(
        ChatMessage.timestamp >= since,
        ChatMessage.role == "assistant",
    )
    if aids is not None:
        chat_stmt = chat_stmt.where(ChatMessage.agent_id.in_(aids))
    chat_result = await db.execute(chat_stmt)
    chat_cost = float(chat_result.scalar() or 0)

    completed_result = await db.execute(
        _scope_task(select(func.count(Task.id)).where(
            Task.created_at >= since,
            Task.status == TaskStatus.COMPLETED,
        ))
    )
    completed = completed_result.scalar() or 0
    total_tasks = task_row.total or 0
    success_rate = round(completed / total_tasks * 100, 1) if total_tasks else 0.0

    # Total time saved across all skill usages in the period
    savings_stmt = select(func.sum(SkillTaskUsage.time_saved_seconds)).where(
        SkillTaskUsage.created_at >= since,
        SkillTaskUsage.time_saved_seconds.isnot(None),
    )
    if aids is not None:
        savings_stmt = savings_stmt.where(SkillTaskUsage.agent_id.in_(aids))
    savings_result = await db.execute(savings_stmt)
    total_time_saved_seconds = int(savings_result.scalar() or 0)

    # Active agents
    agents_stmt = select(func.count(Agent.id)).where(
        Agent.state.in_([AgentState.RUNNING, AgentState.IDLE])
    )
    if aids is not None:
        agents_stmt = agents_stmt.where(Agent.id.in_(aids))
    agents_result = await db.execute(agents_stmt)
    active_agents = agents_result.scalar() or 0

    # Avg task rating
    rating_stmt = select(func.avg(TaskRating.rating)).where(TaskRating.created_at >= since)
    if aids is not None:
        rating_stmt = rating_stmt.where(TaskRating.agent_id.in_(aids))
    avg_rating_result = await db.execute(rating_stmt)
    avg_rating = avg_rating_result.scalar()

    # Daily task volume for sparkline (last `days` days)
    from sqlalchemy import text as sa_text
    daily_result = await db.execute(
        sa_text(f"""
            SELECT date_trunc('day', created_at) AS day,
                   COUNT(id) AS count,
                   COALESCE(SUM(cost_usd), 0) AS cost
            FROM tasks
            WHERE created_at >= :since
                  {"AND agent_id = ANY(:aids)" if aids is not None else ""}
            GROUP BY date_trunc('day', created_at)
            ORDER BY date_trunc('day', created_at)
        """),
        {"since": since, **({"aids": aids} if aids is not None else {})},
    )
    daily_rows = daily_result.all()
    daily_tasks = [{"date": str(r.day)[:10], "count": r.count, "cost": float(r.cost or 0)} for r in daily_rows]

    return {
        "period_days": days,
        "total_tasks": total_tasks,
        "completed_tasks": completed,
        "success_rate_pct": success_rate,
        "total_cost_usd": round(float(task_row.total_cost or 0) + chat_cost, 4),
        "total_task_cost_usd": round(float(task_row.total_cost or 0), 4),
        "total_chat_cost_usd": round(chat_cost, 4),
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
    """Per-skill analytics: time savings vs manual, rating trend, usage stats.

    The skill CATALOG is platform-shared, but the usage aggregates (cost, time saved,
    ratings) are scoped to the caller's own agents — admins see platform-wide."""
    since = _days_ago(days)

    vids = await visible_agent_ids(user, db)
    aids = list(vids) if vids is not None else None

    # All active skills — show even with 0 usage so dashboard is never empty
    skills_result = await db.execute(
        select(Skill).where(Skill.status == "active").order_by(Skill.usage_count.desc(), Skill.name).limit(limit)
    )
    skills = list(skills_result.scalars().all())

    skill_ids = [s.id for s in skills]

    # Aggregate usage data for the period (scoped to the caller's agents)
    usage_where = [
        SkillTaskUsage.skill_id.in_(skill_ids),
        SkillTaskUsage.created_at >= since,
    ]
    if aids is not None:
        usage_where.append(SkillTaskUsage.agent_id.in_(aids))
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
        .where(*usage_where)
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
        raise HTTPException(status_code=404, detail="Skill not found")

    # Usage trend scoped to the caller's own agents (admins see platform-wide).
    vids = await visible_agent_ids(user, db)
    aids = list(vids) if vids is not None else None
    if aids is not None and not aids:
        return {
            "skill_id": skill_id,
            "skill_name": skill.name,
            "manual_duration_seconds": skill.manual_duration_seconds,
            "trend": [],
        }

    from sqlalchemy import text as sa_text
    weekly = await db.execute(
        sa_text(f"""
            SELECT date_trunc('week', created_at) AS week,
                   COUNT(id) AS uses,
                   AVG(skill_helpfulness) AS avg_helpfulness,
                   AVG(user_rating) AS avg_user_rating,
                   AVG(agent_self_rating) AS avg_agent_rating,
                   SUM(time_saved_seconds) AS time_saved
            FROM skill_task_usages
            WHERE skill_id = :skill_id AND created_at >= :since
                  {"AND agent_id = ANY(:aids)" if aids is not None else ""}
            GROUP BY date_trunc('week', created_at)
            ORDER BY date_trunc('week', created_at)
        """),
        {"skill_id": skill_id, "since": since, **({"aids": aids} if aids is not None else {})},
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

async def rework_task_ids(db, agent_ids: list[str], since) -> dict[str, dict[str, set[str]]]:
    """Aufgaben je Agent, die noch einmal angefasst werden mussten.

    Zwei Signale, beide schon in den Daten — es wird nichts zusaetzlich erhoben:

    * ``resumed`` — ``metadata.resumed_from_task``: der Lauf lief nicht in einem Zug
      durch und wurde fortgesetzt (dieselbe Markierung wie in der Aktivitaets-Zeitachse)
    * ``poor`` — Bewertung 1 oder 2: der Mensch hat die Arbeit zurueckgegeben

    Getrennt zurueckgegeben, weil eine Aufgabe beides sein kann — die Quote nutzt die
    Vereinigung, die Aufschluesselung die einzelnen Mengen. Eine Stelle fuer die Regel,
    damit Agenten-Tabelle und Entwicklungs-Karte nicht auseinanderlaufen.
    """
    out: dict[str, dict[str, set[str]]] = {
        aid: {"resumed": set(), "poor": set()} for aid in agent_ids
    }
    if not agent_ids:
        return out

    rows = (await db.execute(
        select(Task.id, Task.agent_id, Task.metadata_)
        .where(Task.agent_id.in_(agent_ids), Task.created_at >= since)
    )).all()
    for tid, aid, meta in rows:
        if (meta or {}).get("resumed_from_task"):
            out.setdefault(aid, {"resumed": set(), "poor": set()})["resumed"].add(tid)

    try:
        poor = (await db.execute(
            select(TaskRating.task_id, TaskRating.agent_id).where(
                TaskRating.agent_id.in_(agent_ids),
                TaskRating.created_at >= since,
                TaskRating.rating <= 2,
            )
        )).all()
        for tid, aid in poor:
            out.setdefault(aid, {"resumed": set(), "poor": set()})["poor"].add(tid)
    except Exception:  # noqa: BLE001 — ohne Bewertungen bleiben die Fortsetzungen
        logger.debug("Schlechte Bewertungen nicht ladbar", exc_info=True)

    return out


def rework_union(entry: dict[str, set[str]] | None) -> set[str]:
    """Vereinigung beider Signale — ein Fall mit beidem zaehlt einmal."""
    if not entry:
        return set()
    return entry["resumed"] | entry["poor"]


@router.get("/agents")
async def get_agents_analytics(
    days: int = Query(30, ge=1, le=365),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Per-agent performance: task volume, success rate, cost, avg rating.

    Scoped to the caller's own agents (admins see all) — this feeds the dashboard's
    Cost-Attribution / Top-Agents card, which must not expose other tenants.
    """
    since = _days_ago(days)

    vids = await visible_agent_ids(user, db)
    agents_stmt = select(Agent)
    if vids is not None:
        if not vids:
            return {"period_days": days, "agents": []}
        agents_stmt = agents_stmt.where(Agent.id.in_(list(vids)))
    agents_result = await db.execute(agents_stmt)
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
    rework_by_agent = await rework_task_ids(db, agent_ids, since)

    result = []
    for agent in agents:
        t = task_by_agent.get(agent.id)
        r = rating_by_agent.get(agent.id)
        total = t.total if t else 0
        rework = len(rework_union(rework_by_agent.get(agent.id)))
        result.append({
            "rework_count": rework,
            "rework_rate_pct": round(rework / total * 100, 1) if total else 0.0,
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
    # Ownership: a non-admin may only inspect their own/shared agents.
    vids = await visible_agent_ids(user, db)
    if vids is not None and agent_id not in vids:
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
                   COUNT(id) FILTER (WHERE status = 'COMPLETED') AS completed,
                   COUNT(id) FILTER (WHERE status = 'FAILED') AS failed
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


@router.get("/agents/{agent_id}/development")
async def agent_development(
    agent_id: str,
    days: int = Query(30, ge=7, le=180),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Wird dieser Agent messbar besser? Und war die Probezeit erfolgreich?

    Setzt NUR vorhandene Daten zusammen — Bewertungen, Fehlerquote der Aufgaben und die
    Plan-Treue (geplant vs. erledigt, seit es den sichtbaren Tagesplan gibt). Bisher sah
    man Kosten und Laufzahl, aber nirgends, ob die Arbeit besser wird.
    """
    from datetime import datetime, timedelta, timezone

    from app.core.ownership import is_admin, visible_agent_ids
    from app.models.agent import Agent
    from app.models.agent_plan_item import AgentPlanItem
    from app.models.task import Task

    if not is_admin(user):
        vids = await visible_agent_ids(user, db)
        if vids is not None and agent_id not in vids:
            raise HTTPException(status_code=403, detail="Access denied")

    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    half = now - timedelta(days=days // 2)

    task_rows = (await db.execute(
        select(Task.id, Task.status, Task.created_at, Task.metadata_).where(
            Task.agent_id == agent_id, Task.created_at >= since
        )
    )).all()

    def _task_stats(rows):
        failed = sum(1 for _, st, _, _ in rows
                     if str(getattr(st, "value", st)).lower() == "failed")
        return len(rows), failed

    recent_rows = [r for r in task_rows if r[2] and r[2] >= half]
    total, failed = _task_stats(task_rows)
    recent_total, recent_failed = _task_stats(recent_rows)
    older_total, older_failed = total - recent_total, failed - recent_failed

    def _rate(f, t):
        return round(100.0 * f / t, 1) if t else 0.0

    # Plan-Treue: von dem, was er sich vorgenommen hat, wie viel wurde erledigt?
    plan_rows = (await db.execute(
        select(AgentPlanItem.status).where(
            AgentPlanItem.agent_id == agent_id, AgentPlanItem.plan_date >= since.date()
        )
    )).scalars().all()
    planned = sum(1 for st in plan_rows if st != "dropped")
    done = sum(1 for st in plan_rows if st == "done")

    # Bewertungen: der bestehende Rating-Pfad ueber Tasks.
    ratings = []
    try:
        from app.models.task_rating import TaskRating
        ratings = (await db.execute(
            select(TaskRating.rating, TaskRating.created_at)
            .where(TaskRating.agent_id == agent_id, TaskRating.created_at >= since)
        )).all()
    except Exception:  # noqa: BLE001 — ohne Bewertungen bleibt der Rest aussagekraeftig
        logger.debug("Bewertungen fuer %s nicht ladbar", scrub_log(agent_id), exc_info=True)

    def _avg(items):
        vals = [float(r) for r, _ in items if r is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    avg_recent = _avg([r for r in ratings if r[1] and r[1] >= half])
    avg_older = _avg([r for r in ratings if r[1] and r[1] < half])

    # Nacharbeitsquote — dieselbe Regel wie in der Agenten-Tabelle, eine Funktion.
    rework_entry = (await rework_task_ids(db, [agent_id], since)).get(agent_id)
    rework_all = rework_union(rework_entry)
    recent_ids = {tid for tid, _, _, _ in recent_rows}
    rework_recent = rework_all & recent_ids
    rework_rate = _rate(len(rework_all), total)
    rework_rate_recent = _rate(len(rework_recent), recent_total)
    rework_rate_older = _rate(len(rework_all) - len(rework_recent), older_total)
    resumed_count = len(rework_entry["resumed"]) if rework_entry else 0
    poorly_rated = len(rework_entry["poor"]) if rework_entry else 0

    # Ein Wort statt einer Zahlenwueste — dieselbe Lesart wie bei den Skills.
    trend = "zu wenig Daten"
    if total >= 10:
        besser = _rate(recent_failed, recent_total) < _rate(older_failed, older_total)
        if avg_recent is not None and avg_older is not None:
            besser = besser or avg_recent > avg_older
        # Weniger Nacharbeit zaehlt genauso als Fortschritt wie weniger Fehlschlaege:
        # ein Agent, der gleich viele Aufgaben schafft, sie aber nicht mehr zweimal
        # anfassen muss, ist messbar besser geworden.
        besser = besser or rework_rate_recent < rework_rate_older
        schlechter = (
            _rate(recent_failed, recent_total) > _rate(older_failed, older_total) + 5
            or rework_rate_recent > rework_rate_older + 5
        )
        trend = "besser" if besser and not schlechter else ("schlechter" if schlechter else "stabil")

    config = agent.config or {}
    created = agent.created_at
    probation_days = (now - created).days if created else 0
    return {
        "agent_id": agent_id,
        "days": days,
        "tasks": {"total": total, "failed": failed, "failure_rate": _rate(failed, total)},
        "failure_rate_recent": _rate(recent_failed, recent_total),
        "failure_rate_older": _rate(older_failed, older_total),
        "ratings": {"count": len(ratings), "avg_recent": avg_recent, "avg_older": avg_older},
        "rework": {
            "count": len(rework_all),
            "rate": rework_rate,
            "rate_recent": rework_rate_recent,
            "rate_older": rework_rate_older,
            "resumed": resumed_count,
            "poorly_rated": poorly_rated,
        },
        "plan_adherence": {
            "planned": planned, "done": done,
            "rate": round(100.0 * done / planned, 1) if planned else 0.0,
        },
        "trend": trend,
        "probation": {
            "days_active": probation_days,
            "review_due": probation_days >= 7,
            "onboarded": bool(config.get("onboarding_complete")),
            "has_responsibilities": bool((config.get("proactive") or {}).get("responsibilities")),
        },
    }


@router.get("/self-improvement")
async def self_improvement(
    days: int = Query(30, ge=7, le=180),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Was hat die Plattform in diesem Zeitraum dazugelernt?

    Die Mechanik lief laengst — die Nachtschicht schreibt Skill-Entwuerfe, der
    Verbesserungs-Motor ueberarbeitet schlecht bewertete Skills, aus Gespraechen
    entstehen dauerhafte Erinnerungen. Nur sah das niemand: es gab keine Flaeche, auf
    der steht, was der Agent gelernt hat. Dieser Endpunkt setzt ausschliesslich
    vorhandene Daten zusammen, es wird nichts zusaetzlich erhoben.
    """
    from datetime import timedelta

    from app.core.ownership import is_admin, visible_agent_ids
    from app.models.agent_memory import AgentMemory as _Mem  # noqa: F401  (Fallback unten)

    since = _days_ago(days)
    vids = await visible_agent_ids(user, db)

    # --- Skills: was ist neu entstanden, was wurde ueberarbeitet ---------------
    from app.models.skill import Skill, SkillStatus

    skills = (await db.execute(
        select(Skill).where(Skill.created_at >= since).order_by(Skill.created_at.desc())
    )).scalars().all()

    def _origin(skill) -> str:
        by = (skill.created_by or "").lower()
        if by.startswith("reflection"):
            return "nachtschicht"
        if by.startswith("agent:"):
            return "agent"
        if by.startswith("import:"):
            return "import"
        return "mensch"

    drafted = [s for s in skills if str(getattr(s.status, "value", s.status)) == "draft"]
    learned = [s for s in skills if _origin(s) in ("nachtschicht", "agent")]

    improved = (await db.execute(
        select(Skill).where(
            Skill.updated_at >= since,
            Skill.current_version > 1,
        ).order_by(Skill.updated_at.desc()).limit(50)
    )).scalars().all()

    validated = [s for s in improved
                 if str(getattr(s.status, "value", s.status)) == "validated"]
    rolled_back = [s for s in improved
                   if str(getattr(s.status, "value", s.status)) == "rolled_back"]

    # --- Erinnerungen, die aus der Reflexion stammen ---------------------------
    memories = 0
    try:
        rows = await db.execute(sa_text(
            "SELECT count(*) FROM agent_memories "
            "WHERE created_at >= :since AND source = 'reflection' "
            "AND superseded_by IS NULL"
        ), {"since": since})
        memories = int(rows.scalar() or 0)
    except Exception:  # noqa: BLE001 — ohne Spalte bleibt der Rest aussagekraeftig
        logger.debug("Reflexions-Erinnerungen nicht zaehlbar", exc_info=True)

    # --- Naechtliche Laeufe ----------------------------------------------------
    from app.models.reflection_run import ReflectionRun

    runs = (await db.execute(
        select(ReflectionRun).where(ReflectionRun.started_at >= since)
        .order_by(ReflectionRun.started_at.desc()).limit(30)
    )).scalars().all()

    def _run_row(run):
        stats = run.stats or {}
        return {
            "id": run.id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "status": run.status,
            "facts_new": stats.get("facts_new", 0),
            "skills_drafted": stats.get("skills_drafted", 0),
            "kb_entries": stats.get("kb_entries", 0),
        }

    def _skill_row(skill):
        return {
            "id": skill.id,
            "name": skill.name,
            "description": (skill.description or "")[:160],
            "status": str(getattr(skill.status, "value", skill.status)),
            "origin": _origin(skill),
            "version": skill.current_version,
            "usage_count": skill.usage_count,
            "avg_rating": round(skill.avg_rating, 2) if skill.avg_rating else None,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
        }

    return {
        "period_days": days,
        "summary": {
            "skills_learned": len(learned),
            "skills_awaiting_review": len(drafted),
            "skills_improved": len(improved),
            "improvements_kept": len(validated),
            "improvements_reverted": len(rolled_back),
            "memories_from_reflection": memories,
            "reflection_runs": len(runs),
        },
        # Entwuerfe zuerst: das ist das, wo ein Mensch etwas tun soll.
        "awaiting_review": [_skill_row(s) for s in drafted[:20]],
        "learned": [_skill_row(s) for s in learned[:20]],
        "improved": [_skill_row(s) for s in improved[:20]],
        "runs": [_run_row(r) for r in runs],
        "scoped": vids is not None and not is_admin(user),
    }
