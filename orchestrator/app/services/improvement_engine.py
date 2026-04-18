"""Improvement Engine - periodic analysis of task ratings to generate agent insights.

Runs as a background job, analyzing ratings every hour to:
1. Detect performance trends (improving/declining)
2. Identify recurring issues from low-rating comments
3. Generate LLM improvement suggestions when average rating < 3.5 (min 5 ratings)
4. Store suggestions in agent_memories and send notifications when significant changes detected
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.agent import Agent
from app.models.notification import Notification
from app.models.task_rating import TaskRating

# Model used for improvement suggestions (same as task self-reflection)
_SUGGESTION_MODEL = "claude-haiku-4-5-20251001"
# Minimum number of ratings before generating suggestions
_MIN_RATINGS_FOR_SUGGESTION = 5
# Average rating threshold below which suggestions are generated
_SUGGESTION_THRESHOLD = 3.5

logger = logging.getLogger(__name__)

# Run analysis every hour
ANALYSIS_INTERVAL_SECONDS = 3600


class ImprovementEngine:
    """Background service that analyzes task ratings and generates improvement insights."""

    def __init__(self):
        self._running = False

    async def run(self) -> None:
        """Main loop — runs analysis periodically."""
        self._running = True
        logger.info("[ImprovementEngine] Started — analyzing ratings every %ds", ANALYSIS_INTERVAL_SECONDS)

        # Wait a bit before first run to let the system stabilize
        await asyncio.sleep(60)

        while self._running:
            try:
                await self._run_analysis()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ImprovementEngine] Analysis error: {e}")

            await asyncio.sleep(ANALYSIS_INTERVAL_SECONDS)

    async def _run_analysis(self) -> None:
        """Run improvement analysis for all agents with recent ratings."""
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            # Find agents with ratings in the last 7 days
            since = datetime.now(timezone.utc) - timedelta(days=7)
            result = await db.execute(
                select(TaskRating.agent_id)
                .where(TaskRating.created_at >= since)
                .group_by(TaskRating.agent_id)
            )
            agent_ids = [row[0] for row in result.all()]

            if not agent_ids:
                return

            logger.info(f"[ImprovementEngine] Analyzing {len(agent_ids)} agents with recent ratings")

            for agent_id in agent_ids:
                try:
                    await self._analyze_agent(db, agent_id)
                except Exception as e:
                    logger.warning(f"[ImprovementEngine] Failed to analyze agent {agent_id}: {e}")

            await db.commit()

    async def analyze(self, agent_id: str, db: AsyncSession) -> None:
        """Public entry point: analyze a single agent and generate suggestions if needed.

        Called from task_router every 10th completed task.
        """
        try:
            await self._analyze_agent(db, agent_id)
            await db.commit()
        except Exception as e:
            logger.warning(f"[ImprovementEngine] analyze({agent_id}) failed: {e}")

    async def _analyze_agent(self, db: AsyncSession, agent_id: str) -> None:
        """Analyze a single agent's ratings and update its config with insights."""
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            return

        # Get all ratings for this agent
        result = await db.execute(
            select(TaskRating)
            .where(TaskRating.agent_id == agent_id)
            .order_by(TaskRating.created_at.asc())
        )
        ratings = list(result.scalars().all())

        if len(ratings) < 3:
            return  # Need at least 3 ratings for meaningful analysis

        # Calculate metrics
        total = len(ratings)
        avg_rating = sum(r.rating for r in ratings) / total

        # Trend: compare first half vs second half
        mid = total // 2
        first_half_avg = sum(r.rating for r in ratings[:mid]) / mid if mid > 0 else 0
        second_half_avg = sum(r.rating for r in ratings[mid:]) / (total - mid) if (total - mid) > 0 else 0
        trend = round(second_half_avg - first_half_avg, 2)

        # Recent performance (last 5)
        recent = ratings[-5:]
        recent_avg = sum(r.rating for r in recent) / len(recent)

        # Cost efficiency trend
        costs = [r.task_cost_usd for r in ratings if r.task_cost_usd]
        avg_cost = sum(costs) / len(costs) if costs else None

        # Duration trend
        durations = [r.task_duration_ms for r in ratings if r.task_duration_ms]
        avg_duration = sum(durations) / len(durations) if durations else None

        # Collect issues from low ratings
        issues: defaultdict[str, int] = defaultdict(int)
        for r in ratings:
            if r.rating <= 2 and r.comment:
                issues[r.comment.strip()[:100]] += 1
        top_issues = [issue for issue, _ in sorted(issues.items(), key=lambda x: -x[1])[:3]]

        # Build improvement metadata
        improvement = {
            "last_analyzed": datetime.now(timezone.utc).isoformat(),
            "total_ratings": total,
            "average_rating": round(avg_rating, 2),
            "recent_avg_rating": round(recent_avg, 2),
            "rating_trend": trend,  # positive = improving, negative = declining
            "avg_cost_usd": round(avg_cost, 4) if avg_cost else None,
            "avg_duration_ms": round(avg_duration) if avg_duration else None,
            "top_issues": top_issues,
            "status": _classify_performance(avg_rating, trend, recent_avg),
        }

        # Generate LLM improvement suggestion if performance is poor
        suggestion = None
        if total >= _MIN_RATINGS_FOR_SUGGESTION and avg_rating < _SUGGESTION_THRESHOLD:
            suggestion = await _generate_improvement_suggestion(agent, improvement, top_issues)
            if suggestion:
                improvement["latest_suggestion"] = suggestion
                # Persist suggestion to agent_memories so agents can read it
                await _store_suggestion_in_memory(db, agent_id, suggestion)

        # Update agent config
        config = dict(agent.config) if agent.config else {}
        old_improvement = config.get("improvement", {})
        config["improvement"] = improvement
        agent.config = config

        # Send notification if significant change detected
        old_status = old_improvement.get("status")
        new_status = improvement["status"]
        if old_status and old_status != new_status:
            await _send_trend_notification(db, agent, old_status, new_status, improvement)

        log_suffix = f" | suggestion generated" if suggestion else ""
        logger.info(
            f"[ImprovementEngine] Agent '{agent.name}': "
            f"avg={avg_rating:.1f}, trend={trend:+.2f}, status={new_status}{log_suffix}"
        )


async def _generate_improvement_suggestion(
    agent: Agent, improvement: dict, top_issues: list[str]
) -> str | None:
    """Call the LLM to generate a one-paragraph improvement suggestion for the agent.

    Returns the suggestion text, or None if the call fails.
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    issues_text = "\n".join(f"- {issue}" for issue in top_issues) if top_issues else "- No specific issues logged"
    prompt = (
        f"You are an AI performance coach. An AI agent named '{agent.name}' has been performing poorly.\n\n"
        f"Stats (last {improvement['total_ratings']} tasks):\n"
        f"  Average rating: {improvement['average_rating']}/5\n"
        f"  Recent average (last 5): {improvement['recent_avg_rating']}/5\n"
        f"  Rating trend: {'improving' if improvement['rating_trend'] > 0 else 'declining'} "
        f"({improvement['rating_trend']:+.2f})\n"
        f"  Avg cost per task: ${improvement['avg_cost_usd'] or 0:.4f}\n"
        f"  Avg duration: {(improvement['avg_duration_ms'] or 0) / 1000:.1f}s\n\n"
        f"Recurring issues from low-rated tasks:\n{issues_text}\n\n"
        "Write a concise (2-3 sentence) actionable improvement suggestion for this agent. "
        "Focus on what the agent should do differently to get better ratings."
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": _SUGGESTION_MODEL,
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        resp.raise_for_status()
        suggestion = resp.json()["content"][0]["text"].strip()
        return suggestion[:1000]
    except Exception as exc:
        logger.warning(f"[ImprovementEngine] LLM suggestion call failed for agent {agent.id}: {exc}")
        return None


async def _store_suggestion_in_memory(db: AsyncSession, agent_id: str, suggestion: str) -> None:
    """Persist an improvement suggestion to agent_memories (category='improvement')."""
    try:
        from app.models.memory import AgentMemory

        # Upsert: overwrite any existing improvement suggestion for this agent
        result = await db.execute(
            select(AgentMemory).where(
                AgentMemory.agent_id == agent_id,
                AgentMemory.category == "improvement",
                AgentMemory.key == "latest_suggestion",
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.content = suggestion
            existing.updated_at = datetime.now(timezone.utc)
            existing.importance = 5  # High importance so it's not decayed away
        else:
            mem = AgentMemory(
                agent_id=agent_id,
                category="improvement",
                key="latest_suggestion",
                content=suggestion,
                importance=5,
                room="meta:improvement",
            )
            db.add(mem)
        logger.info(f"[ImprovementEngine] Stored improvement suggestion for agent {agent_id}")
    except Exception as exc:
        logger.warning(f"[ImprovementEngine] Could not store suggestion in memory for {agent_id}: {exc}")


def _classify_performance(avg: float, trend: float, recent_avg: float) -> str:
    """Classify agent performance into a status label."""
    if recent_avg >= 4.0 and trend >= 0:
        return "excellent"
    if recent_avg >= 3.5:
        return "good"
    if recent_avg >= 2.5:
        if trend > 0.3:
            return "improving"
        if trend < -0.3:
            return "declining"
        return "average"
    return "needs_attention"


async def _send_trend_notification(
    db: AsyncSession,
    agent: Agent,
    old_status: str,
    new_status: str,
    improvement: dict,
) -> None:
    """Send a notification when an agent's performance status changes."""
    status_labels = {
        "excellent": "🌟 Exzellent",
        "good": "✅ Gut",
        "improving": "📈 Verbessernd",
        "average": "➡️ Durchschnittlich",
        "declining": "📉 Rückläufig",
        "needs_attention": "⚠️ Braucht Aufmerksamkeit",
    }

    old_label = status_labels.get(old_status, old_status)
    new_label = status_labels.get(new_status, new_status)

    is_positive = new_status in ("excellent", "good", "improving")
    notif_type = "success" if is_positive else "warning"

    notif = Notification(
        agent_id=agent.id,
        type=notif_type,
        title=f"Performance-Trend: {agent.name}",
        message=(
            f"Status geändert: {old_label} → {new_label}\n"
            f"Durchschnitt: {improvement['average_rating']}/5 "
            f"(letzte 5: {improvement['recent_avg_rating']}/5)"
        ),
        priority="high" if new_status == "needs_attention" else "normal",
        action_url=f"/agents/{agent.id}",
        meta={"type": "improvement_trend", "improvement": improvement},
    )
    db.add(notif)
