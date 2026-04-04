"""Improvement Engine - periodic analysis of task ratings to generate agent insights.

Runs as a background job, analyzing ratings every hour to:
1. Detect performance trends (improving/declining)
2. Identify recurring issues from low-rating comments
3. Update agent config with improvement metadata
4. Send notifications when significant changes are detected
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.agent import Agent
from app.models.notification import Notification
from app.models.task_rating import TaskRating

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

        logger.info(
            f"[ImprovementEngine] Agent '{agent.name}': "
            f"avg={avg_rating:.1f}, trend={trend:+.2f}, status={new_status}"
        )


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
