"""Task Ratings API - rate completed tasks, view agent improvement reports."""

import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, require_auth_or_agent, verify_agent_token
from app.models.agent import Agent
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.schemas.task_rating import (
    AgentRatingsResponse,
    ImprovementReport,
    TaskRatingCreate,
    TaskRatingResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ratings", tags=["ratings"])


def _to_response(r: TaskRating) -> dict:
    return {
        "id": r.id,
        "task_id": r.task_id,
        "agent_id": r.agent_id,
        "user_id": r.user_id,
        "rating": r.rating,
        "comment": r.comment,
        "task_cost_usd": r.task_cost_usd,
        "task_duration_ms": r.task_duration_ms,
        "task_num_turns": r.task_num_turns,
        "created_at": r.created_at,
    }


class TaskSelfRateBody(BaseModel):
    rating: int  # 1-5
    reflection: str = ""


@router.post("/task-self-rate")
async def task_self_rate(
    body: TaskSelfRateBody,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent self-rates its most recently completed/running task.

    The agent identifies itself via its Bearer token. We look up the
    most recent task for that agent and attach a self-rating to it.
    """
    agent_id = auth["agent_id"]

    if not 1 <= body.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    # Find the most recent completed or running task for this agent
    task_result = await db.execute(
        select(Task)
        .where(Task.agent_id == agent_id)
        .where(Task.status.in_([TaskStatus.COMPLETED, TaskStatus.RUNNING, TaskStatus.FAILED]))
        .order_by(Task.created_at.desc())
        .limit(1)
    )
    task = task_result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="No recent task found for this agent to rate")

    # Check for duplicate self-rating (one per task per agent)
    existing = await db.execute(
        select(TaskRating).where(
            TaskRating.task_id == task.id,
            TaskRating.user_id == f"agent:{agent_id}",
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This task has already been self-rated")

    rating = TaskRating(
        task_id=task.id,
        agent_id=agent_id,
        user_id=f"agent:{agent_id}",
        rating=body.rating,
        comment=body.reflection,
        task_cost_usd=task.cost_usd,
        task_duration_ms=task.duration_ms,
        task_num_turns=task.num_turns,
    )
    db.add(rating)
    await db.commit()
    await db.refresh(rating)
    return _to_response(rating)


@router.post("/tasks/{task_id}/rate", response_model=TaskRatingResponse)
async def rate_task(
    task_id: str,
    body: TaskRatingCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Submit a rating for a completed task. Accepts auth cookie or X-Internal header."""
    # Support internal calls from Telegram bot (no cookie)
    is_internal = request.headers.get("X-Internal") == "telegram-bot"
    if is_internal:
        user_id = "telegram"
    else:
        user = await require_auth(request, db)
        user_id = user.id

    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        raise HTTPException(status_code=400, detail="Can only rate completed or failed tasks")

    # Check for duplicate rating
    existing = await db.execute(
        select(TaskRating).where(
            TaskRating.task_id == task_id,
            TaskRating.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="You already rated this task")

    rating = TaskRating(
        task_id=task_id,
        agent_id=task.agent_id,
        user_id=user_id,
        rating=body.rating,
        comment=body.comment,
        # Snapshot task metadata at rating time
        task_cost_usd=task.cost_usd,
        task_duration_ms=task.duration_ms,
        task_num_turns=task.num_turns,
    )
    db.add(rating)
    await db.commit()
    await db.refresh(rating)

    # Persist negative feedback (< 4★ with comment) as agent memory so it survives task GC
    if body.rating < 4 and body.comment:
        try:
            from app.db.session import async_session_factory
            from app.models.memory import AgentMemory
            async with async_session_factory() as mem_db:
                stars = "★" * body.rating + "☆" * (5 - body.rating)
                mem = AgentMemory(
                    agent_id=task.agent_id,
                    category="correction",
                    key="user_feedback",
                    content=f"{stars} User-Feedback zu Task '{task.title or task_id}': {body.comment}",
                    importance=5,
                    confidence=1.5,  # user-corrected → never auto-decay
                )
                mem_db.add(mem)
                await mem_db.commit()
        except Exception as e:
            logger.warning(f"Could not save feedback memory: {e}")

        # If this task produced a skill, queue a follow-up task to update it
        try:
            from app.models.skill import Skill
            from app.core.task_router import TaskRouter
            from app.core.load_balancer import LoadBalancer
            from app.services.redis_service import RedisService
            from app.config import settings as app_settings

            skill_result = await db.execute(
                select(Skill).where(Skill.source_task_id == task_id)
            )
            skill = skill_result.scalar_one_or_none()
            if skill:
                stars = "★" * body.rating + "☆" * (5 - body.rating)
                followup_prompt = (
                    f"Der Nutzer hat deinen Task '{task.title or task_id}' mit {stars} bewertet.\n\n"
                    f"Feedback: \"{body.comment}\"\n\n"
                    f"Du hast in diesem Task den Skill **'{skill.name}'** (ID: {skill.id}) erstellt.\n"
                    f"Überarbeite diesen Skill jetzt mit `skill_update` basierend auf dem Feedback.\n"
                    f"Rufe danach `rate_task` mit 5★ auf (diese Korrektur-Aufgabe)."
                )
                redis = RedisService(redis_url=app_settings.redis_url)
                await redis.connect()
                lb = LoadBalancer(redis)
                router = TaskRouter(db, redis, lb)
                await router.create_and_route_task(
                    title=f"Skill Update: {skill.name}",
                    prompt=followup_prompt,
                    agent_id=task.agent_id,
                    priority=8,
                )
                await redis.disconnect()
                logger.info(f"Queued skill-update follow-up for skill {skill.id} after {body.rating}★ feedback")
        except Exception as e:
            logger.warning(f"Could not queue skill-update follow-up: {e}")

    return _to_response(rating)


@router.get("/agents/{agent_id}/ratings", response_model=AgentRatingsResponse)
async def get_agent_ratings(
    agent_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """List all ratings for an agent, newest first."""
    # Verify agent exists
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    if not agent_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Agent not found")

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(TaskRating).where(TaskRating.agent_id == agent_id)
    )
    total = count_result.scalar() or 0

    # Average rating
    avg_result = await db.execute(
        select(func.avg(TaskRating.rating)).where(TaskRating.agent_id == agent_id)
    )
    avg_rating = avg_result.scalar()
    avg_rating = round(float(avg_rating), 2) if avg_rating is not None else None

    # Paginated ratings
    query = (
        select(TaskRating)
        .where(TaskRating.agent_id == agent_id)
        .order_by(TaskRating.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    ratings = list(result.scalars().all())

    return {
        "ratings": [_to_response(r) for r in ratings],
        "total": total,
        "average_rating": avg_rating,
    }


@router.get("/agents/{agent_id}/improvement-report", response_model=ImprovementReport)
async def get_improvement_report(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Generate an improvement report for an agent based on ratings and task history."""
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Fetch all ratings for this agent, ordered by time
    result = await db.execute(
        select(TaskRating)
        .where(TaskRating.agent_id == agent_id)
        .order_by(TaskRating.created_at.asc())
    )
    ratings = list(result.scalars().all())

    total = len(ratings)
    if total == 0:
        return {
            "agent_id": agent_id,
            "agent_name": agent.name,
            "total_ratings": 0,
            "average_rating": None,
            "rating_trend": [],
            "cost_trend": [],
            "duration_trend": [],
            "top_issues": [],
            "summary": "No ratings yet. Complete tasks and rate them to build an improvement report.",
        }

    avg_rating = round(sum(r.rating for r in ratings) / total, 2)

    # Compute rolling average trend (windows of 5)
    window = 5
    rating_trend = []
    for i in range(0, total, window):
        chunk = ratings[i : i + window]
        rating_trend.append(round(sum(r.rating for r in chunk) / len(chunk), 2))

    # Cost and duration trends (same windows)
    cost_trend = []
    duration_trend = []
    for i in range(0, total, window):
        chunk = ratings[i : i + window]
        costs = [r.task_cost_usd for r in chunk if r.task_cost_usd is not None]
        durations = [r.task_duration_ms for r in chunk if r.task_duration_ms is not None]
        cost_trend.append(round(sum(costs) / len(costs), 4) if costs else None)
        duration_trend.append(round(sum(durations) / len(durations)) if durations else None)

    # Extract top issues from low-rating comments
    issues: defaultdict[str, int] = defaultdict(int)
    for r in ratings:
        if r.rating <= 2 and r.comment:
            # Use the comment itself as an issue (could do NLP clustering later)
            issues[r.comment.strip()[:100]] += 1
    top_issues = [issue for issue, _ in sorted(issues.items(), key=lambda x: -x[1])[:5]]

    # Build summary
    trend_direction = ""
    if len(rating_trend) >= 2:
        if rating_trend[-1] > rating_trend[0]:
            trend_direction = "Ratings are improving over time."
        elif rating_trend[-1] < rating_trend[0]:
            trend_direction = "Ratings are declining — review recent task quality."
        else:
            trend_direction = "Ratings are stable."

    summary = (
        f"{agent.name} has {total} ratings with an average of {avg_rating}/5. "
        f"{trend_direction}"
    )
    if top_issues:
        summary += f" Top issues reported: {'; '.join(top_issues[:3])}"

    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "total_ratings": total,
        "average_rating": avg_rating,
        "rating_trend": rating_trend,
        "cost_trend": cost_trend,
        "duration_trend": duration_trend,
        "top_issues": top_issues,
        "summary": summary,
    }
