"""Improvement Engine - periodic analysis of task ratings + skill quality.

Runs as a background job, analyzing ratings every hour to:
1. Detect performance trends (improving/declining)
2. Identify recurring issues from low-rating comments
3. Generate LLM improvement suggestions when average rating < 3.5 (min 5 ratings)
4. Store suggestions in agent_memories and send notifications when significant changes detected
5. Auto-improve skill content when avg helpfulness < 3.0 (min 5 rated usages)
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
from app.models.skill import Skill, SkillTaskUsage
from app.services.skill_plateau import is_plateaued

logger = logging.getLogger(__name__)

# Hardcoded defaults — overridable via PlatformSettings and per-agent config
_DEFAULTS = {
    "suggestion_model": "claude-haiku-4-5-20251001",
    "min_ratings": 5,
    "suggestion_threshold": 3.5,
    "min_skill_usages": 5,
    "skill_threshold": 3.0,
    "analysis_interval": 3600,
}

# PlatformSettings key → default key mapping
_SETTINGS_KEY_MAP = {
    "improvement_suggestion_model": "suggestion_model",
    "improvement_min_ratings": "min_ratings",
    "improvement_suggestion_threshold": "suggestion_threshold",
    "improvement_min_skill_usages": "min_skill_usages",
    "improvement_skill_threshold": "skill_threshold",
    "improvement_analysis_interval": "analysis_interval",
}


async def _load_thresholds(db, agent: Agent | None = None) -> dict:
    """Load improvement thresholds: agent config > PlatformSettings > hardcoded defaults."""
    from app.models.platform_settings import PlatformSettings

    thresholds = dict(_DEFAULTS)

    # Layer 1: global overrides from PlatformSettings
    result = await db.execute(
        select(PlatformSettings).where(
            PlatformSettings.key.in_(list(_SETTINGS_KEY_MAP.keys()))
        )
    )
    for row in result.scalars().all():
        default_key = _SETTINGS_KEY_MAP.get(row.key)
        if default_key and row.value:
            expected_type = type(_DEFAULTS[default_key])
            try:
                if expected_type is int:
                    thresholds[default_key] = int(row.value)
                elif expected_type is float:
                    thresholds[default_key] = float(row.value)
                else:
                    thresholds[default_key] = row.value
            except (ValueError, TypeError):
                pass  # keep hardcoded default

    # Layer 2: per-agent overrides from agent.config["improvement_thresholds"]
    if agent and agent.config:
        overrides = agent.config.get("improvement_thresholds") or {}
        for key in _DEFAULTS:
            if key in overrides and overrides[key] is not None:
                expected_type = type(_DEFAULTS[key])
                try:
                    if expected_type is int:
                        thresholds[key] = int(overrides[key])
                    elif expected_type is float:
                        thresholds[key] = float(overrides[key])
                    else:
                        thresholds[key] = overrides[key]
                except (ValueError, TypeError):
                    pass

    return thresholds


class ImprovementEngine:
    """Background service that analyzes task ratings and generates improvement insights."""

    def __init__(self):
        self._running = False

    async def run(self) -> None:
        """Main loop — runs analysis periodically."""
        self._running = True
        interval = _DEFAULTS["analysis_interval"]
        logger.info("[ImprovementEngine] Started — analyzing ratings every %ds", interval)

        # Wait a bit before first run to let the system stabilize
        await asyncio.sleep(60)

        while self._running:
            try:
                interval = await self._run_analysis()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[ImprovementEngine] Analysis error: %s", e, exc_info=True)

            await asyncio.sleep(interval)

    async def _run_analysis(self) -> int:
        """Run improvement analysis for all agents with recent ratings.

        Returns the analysis interval for the next sleep cycle.
        """
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            thresholds = await _load_thresholds(db)
            interval = thresholds["analysis_interval"]

            # Find agents with ratings in the last 7 days
            since = datetime.now(timezone.utc) - timedelta(days=7)
            result = await db.execute(
                select(TaskRating.agent_id)
                .where(TaskRating.created_at >= since)
                .group_by(TaskRating.agent_id)
            )
            agent_ids = [row[0] for row in result.all()]

            if not agent_ids:
                return interval

            logger.info(f"[ImprovementEngine] Analyzing {len(agent_ids)} agents with recent ratings")

            for agent_id in agent_ids:
                try:
                    await self._analyze_agent(db, agent_id)
                except Exception as e:
                    logger.warning(f"[ImprovementEngine] Failed to analyze agent {agent_id}: {e}")

            # Validate skills currently in probation (before dispatching new improvements)
            try:
                await _validate_probation_skills(db)
            except Exception as e:
                logger.warning(f"[ImprovementEngine] Skill probation validation failed: {e}")

            # Also analyze skill quality across all agents
            try:
                await _improve_poorly_rated_skills(db)
            except Exception as e:
                logger.warning(f"[ImprovementEngine] Skill improvement pass failed: {e}")

            await db.commit()
            return interval

    async def analyze(self, agent_id: str, db: AsyncSession) -> None:
        """Public entry point: analyze a single agent and generate suggestions if needed.

        Called from task_router every 10th completed task.
        """
        try:
            await self._analyze_agent(db, agent_id)
            await _validate_probation_skills(db)
            await _improve_poorly_rated_skills(db)
            await db.commit()
        except Exception as e:
            logger.warning(f"[ImprovementEngine] analyze({agent_id}) failed: {e}")

    async def _analyze_agent(self, db: AsyncSession, agent_id: str) -> None:
        """Analyze a single agent's ratings and update its config with insights."""
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if not agent:
            return

        thresholds = await _load_thresholds(db, agent)

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
        min_ratings = thresholds["min_ratings"]
        suggestion_threshold = thresholds["suggestion_threshold"]
        if total >= min_ratings and avg_rating < suggestion_threshold:
            suggestion = await _generate_improvement_suggestion(
                agent, improvement, top_issues, thresholds["suggestion_model"]
            )
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
    agent: Agent, improvement: dict, top_issues: list[str], model: str | None = None,
) -> str | None:
    """Call the LLM to generate a one-paragraph improvement suggestion for the agent.

    Returns the suggestion text, or None if the call fails.
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        return None
    suggestion_model = model or _DEFAULTS["suggestion_model"]

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
                    "model": suggestion_model,
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


_PROBATION_MIN_USAGES = 5  # minimum new usages during probation before validating
_PROBATION_MAX_DAYS = 14   # force validation after this many days even with few usages


async def _validate_probation_skills(db: AsyncSession) -> None:
    """Check skills in probation and validate/rollback based on post-improvement ratings."""
    from app.models.skill import SkillVersion

    skills_in_probation = (await db.execute(
        select(Skill).where(Skill.improvement_status == "probation")
    )).scalars().all()

    if not skills_in_probation:
        return

    now = datetime.now(timezone.utc)

    for skill in skills_in_probation:
        probation_start = skill.probation_started_at
        if not probation_start:
            skill.improvement_status = None
            continue

        probation_start_aware = probation_start.replace(tzinfo=timezone.utc) if probation_start.tzinfo is None else probation_start

        # Count rated usages since probation started
        post_improvement = (await db.execute(
            select(
                func.count(SkillTaskUsage.id).label("count"),
                func.avg(SkillTaskUsage.skill_helpfulness).label("avg_h"),
            )
            .where(
                SkillTaskUsage.skill_id == skill.id,
                SkillTaskUsage.skill_helpfulness.isnot(None),
                SkillTaskUsage.created_at >= probation_start_aware,
            )
        )).one()

        post_count = int(post_improvement.count or 0)
        days_in_probation = (now - probation_start_aware).days

        # Not enough data yet and still within time window
        if post_count < _PROBATION_MIN_USAGES and days_in_probation < _PROBATION_MAX_DAYS:
            continue

        pre_avg = skill.pre_improvement_avg_helpfulness or 0.0
        post_avg = float(post_improvement.avg_h) if post_improvement.avg_h is not None else 0.0

        if post_avg > pre_avg or (post_count < _PROBATION_MIN_USAGES and days_in_probation >= _PROBATION_MAX_DAYS):
            # Improved or timed out with insufficient data (keep new version)
            skill.improvement_status = "validated"
            logger.info(
                f"[ImprovementEngine] Skill '{skill.name}' (id={skill.id}) VALIDATED: "
                f"pre={pre_avg:.1f} → post={post_avg:.1f} over {post_count} usages"
            )
        else:
            # Rollback: restore content from the latest version snapshot
            latest_version = (await db.execute(
                select(SkillVersion)
                .where(SkillVersion.skill_id == skill.id)
                .order_by(SkillVersion.version_number.desc())
                .limit(1)
            )).scalar_one_or_none()

            if latest_version:
                skill.content = latest_version.content
                skill.description = latest_version.description or skill.description
                logger.info(
                    f"[ImprovementEngine] Skill '{skill.name}' (id={skill.id}) ROLLED BACK: "
                    f"pre={pre_avg:.1f} → post={post_avg:.1f} (worse). Restored v{latest_version.version_number}"
                )
            else:
                logger.warning(
                    f"[ImprovementEngine] Skill '{skill.name}' (id={skill.id}) needs rollback "
                    f"but no version snapshot found"
                )
            skill.improvement_status = "rolled_back"

        skill.probation_started_at = None
        skill.pre_improvement_avg_helpfulness = None
        skill.pre_improvement_rated_count = None

    await db.flush()


async def _generate_skill_improvement(
    skill: Skill, avg_h: float, rated_count: int, model: str | None = None,
) -> str | None:
    """Call the LLM to rewrite a skill's content. Returns new content or None."""
    api_key = settings.anthropic_api_key
    if not api_key:
        return None
    suggestion_model = model or _DEFAULTS["suggestion_model"]

    prompt = (
        f"You are a skill-content editor. The skill '{skill.name}' has a low "
        f"helpfulness rating ({avg_h:.1f}/5 over {rated_count} rated uses).\n\n"
        f"CURRENT CONTENT:\n{skill.content or '(empty)'}\n\n"
        "Rewrite the skill content to be clearer, explicitly step-by-step, and "
        "more actionable. Keep the same purpose. Make steps concrete, add short "
        "examples where helpful. Output ONLY the rewritten markdown content — no "
        "preamble, no explanation."
    )
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": suggestion_model,
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"].strip()
        return content or None
    except Exception as exc:
        logger.warning(f"[ImprovementEngine] Skill rewrite call failed for skill {skill.id}: {exc}")
        return None


async def _skill_helpfulness_history(db: AsyncSession, skill_id: int, limit: int = 4) -> list[float]:
    """Return the most recent per-version avg helpfulness snapshots, chronological."""
    from app.models.skill import SkillVersion

    rows = (await db.execute(
        select(SkillVersion.avg_helpfulness_at_snapshot)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.version_number.desc())
        .limit(limit)
    )).scalars().all()
    # rows come newest-first; reverse to chronological and drop NULL snapshots
    return [float(h) for h in reversed(rows) if h is not None]


async def _skill_changed_dimensions(db: AsyncSession, skill_id: int, limit: int = 4) -> list[str]:
    """Best-effort extraction of dimensions changed in recent versions.

    Dimensions are read from SkillVersion.change_reason when it carries a JSON
    payload with a "changed_dimensions" list (written by the plateau-aware
    improvement path). Older/plain change_reason strings contribute nothing —
    the recommendation then just steers toward any unchanged dimension.
    """
    from app.models.skill import SkillVersion

    rows = (await db.execute(
        select(SkillVersion.change_reason)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.version_number.desc())
        .limit(limit)
    )).scalars().all()
    dims: list[str] = []
    for reason in rows:
        if not reason:
            continue
        try:
            payload = json.loads(reason)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            for d in payload.get("changed_dimensions", []) or []:
                if isinstance(d, str):
                    dims.append(d)
    return dims


async def _handle_skill_plateau(db: AsyncSession, skill: "Skill") -> None:
    """Emit an alternative-strategy alert + KB entry when a skill has plateaued.

    Called instead of generating yet another generic rewrite once the last few
    versions show no significant rating gain.
    """
    from app.services.skill_plateau import recommend_alternative_strategy
    from app.models.knowledge import KnowledgeEntry

    dims = await _skill_changed_dimensions(db, skill.id)
    rec = recommend_alternative_strategy(dims)

    db.add(Notification(
        agent_id="system",
        type="warning",
        title=f"Skill-Plateau: '{skill.name}' verbessert sich nicht mehr",
        message=(
            f"Mehrere Versionen ohne signifikante Verbesserung. {rec['message']} "
            f"Empfohlene nächste Dimension: {rec['recommended_dimension']}."
        )[:240],
        priority="high",
        action_url=f"/skills/{skill.id}",
        meta={
            "type": "skill_plateau",
            "skill_id": skill.id,
            "skill_name": skill.name,
            "recommended_dimension": rec["recommended_dimension"],
            "tried_dimensions": rec["tried"],
        },
    ))

    title = f"Skill-Plateau: {skill.name}"
    body = (
        f"# {title}\n\n"
        f"Der Skill **{skill.name}** (id {skill.id}) steckt in einem "
        f"Verbesserungs-Plateau: die letzten Versionen brachten keine "
        f"signifikante Rating-Steigerung (< 0.2).\n\n"
        f"## Empfehlung\n{rec['message']}\n\n"
        f"- Bereits (erfolglos) geänderte Dimensionen: "
        f"{', '.join(rec['tried']) or '—'}\n"
        f"- Nächste zu testende Dimension (nur EINE ändern): "
        f"**{rec['recommended_dimension']}**\n"
        f"- Weitere Kandidaten: {', '.join(rec['candidates'])}\n\n"
        f"#skill-improvement #plateau [[Skill improvement plateau]]\n"
    )
    existing = (await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.title == title)
    )).scalar_one_or_none()
    if existing:
        existing.content = body
        existing.updated_by = "improvement_engine"
    else:
        db.add(KnowledgeEntry(
            title=title,
            content=body,
            tags=["skill-improvement", "plateau"],
            created_by="improvement_engine",
            updated_by="improvement_engine",
        ))

    skill.improvement_status = "plateau"
    logger.info(
        "[ImprovementEngine] Skill '%s' (id=%s) PLATEAU — recommended dimension '%s'",
        skill.name, skill.id, rec["recommended_dimension"],
    )


async def _improve_poorly_rated_skills(db: AsyncSession) -> None:
    """Find skills with low helpfulness ratings and create improvement *proposals*.

    Triggered hourly. The engine generates rewritten content via the LLM and
    stores it as a proposal on the skill (`improvement_status = "pending_review"`).
    A user must approve the proposal in the marketplace UI before it is applied —
    nothing is overwritten automatically. Skills already pending review or in
    probation, or improved within 24h, are skipped. Works for skills with no
    assigned agent too (imported skills no longer fall through).
    """
    thresholds = await _load_thresholds(db)
    min_usages = thresholds["min_skill_usages"]
    skill_threshold = thresholds["skill_threshold"]

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    agg = (await db.execute(
        select(
            SkillTaskUsage.skill_id,
            func.count(SkillTaskUsage.id).label("rated_count"),
            func.avg(SkillTaskUsage.skill_helpfulness).label("avg_helpfulness"),
        )
        .where(SkillTaskUsage.skill_helpfulness.isnot(None))
        .group_by(SkillTaskUsage.skill_id)
        .having(func.count(SkillTaskUsage.id) >= min_usages)
        .having(func.avg(SkillTaskUsage.skill_helpfulness) <= skill_threshold)
    )).all()

    if not agg:
        return

    skill_ids = [row.skill_id for row in agg]
    skills = {s.id: s for s in (await db.execute(
        select(Skill).where(Skill.id.in_(skill_ids), Skill.status == "active")
    )).scalars().all()}

    for row in agg:
        skill = skills.get(row.skill_id)
        if not skill:
            continue
        # Skip if recently improved
        if skill.updated_at and skill.updated_at.replace(tzinfo=timezone.utc) > since_24h:
            continue
        # Skip if already pending review or in probation
        if skill.improvement_status in ("pending_review", "probation"):
            continue

        avg_h = float(row.avg_helpfulness)
        rated_count = int(row.rated_count)

        # Plateau guard: if the last few versions produced no real gain, stop
        # generating another same-flavour rewrite and instead recommend a
        # different strategy (alternative dimension). Runs at most once per
        # plateau — the "plateau" status is cleared when a new version lands.
        history = await _skill_helpfulness_history(db, skill.id)
        if is_plateaued(history + [avg_h]) and skill.improvement_status != "plateau":
            await _handle_skill_plateau(db, skill)
            continue

        new_content = await _generate_skill_improvement(skill, avg_h, rated_count)
        if not new_content or new_content.strip() == (skill.content or "").strip():
            logger.info(f"[ImprovementEngine] No usable rewrite for skill '{skill.name}' — skipping")
            continue

        skill.improvement_status = "pending_review"
        skill.improvement_review_reason = "low_helpfulness"
        skill.improvement_proposed_at = datetime.now(timezone.utc)
        skill.improvement_proposal = {
            "old_content": skill.content or "",
            "old_description": skill.description or "",
            "suggested_content": new_content,
            "suggested_description": skill.description or "",
            "reason": "low_helpfulness",
            "avg_helpfulness_before": avg_h,
            "rated_count_before": rated_count,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(
            f"[ImprovementEngine] Improvement proposal created for skill "
            f"'{skill.name}' (id={skill.id}) — awaiting user review"
        )
        # Notify users whose feedback triggered this proposal
        await _notify_feedback_contributors(db, skill, avg_h, rated_count)

    await db.commit()


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


async def _notify_feedback_contributors(
    db: AsyncSession,
    skill: "Skill",
    avg_helpfulness: float,
    rated_count: int,
) -> None:
    """Notify users whose low ratings triggered a skill improvement.

    Finds all users who rated tasks using this skill with ≤3 stars in the last 7 days
    and creates a notification telling them their feedback is being acted upon.
    """
    since = datetime.now(timezone.utc) - timedelta(days=7)

    # Find task_ids from recent low-rated skill usages
    usage_result = await db.execute(
        select(SkillTaskUsage.task_id)
        .where(
            SkillTaskUsage.skill_id == skill.id,
            SkillTaskUsage.user_rating.isnot(None),
            SkillTaskUsage.user_rating <= 3,
            SkillTaskUsage.created_at >= since,
        )
    )
    task_ids = [row[0] for row in usage_result.all()]

    if not task_ids:
        return

    # Find unique users who rated those tasks
    rating_result = await db.execute(
        select(TaskRating.user_id, TaskRating.agent_id)
        .where(TaskRating.task_id.in_(task_ids))
        .group_by(TaskRating.user_id, TaskRating.agent_id)
    )
    contributors = rating_result.all()

    if not contributors:
        return

    for row in contributors:
        user_id = row[0]
        agent_id = row[1]
        notif = Notification(
            agent_id=agent_id,
            type="info",
            title=f"Dein Feedback wirkt: Skill '{skill.name}' wird verbessert",
            message=(
                f"Dein Feedback hat eine automatische Verbesserung ausgelöst. "
                f"Der Skill hatte {avg_helpfulness:.1f}/5 Durchschnitt über {rated_count} Nutzungen — "
                f"ein Agent arbeitet jetzt an einer besseren Version."
            ),
            priority="normal",
            action_url=f"/skills/{skill.id}",
            meta={
                "type": "skill_feedback_triggered",
                "skill_id": skill.id,
                "skill_name": skill.name,
                "avg_helpfulness": avg_helpfulness,
                "rated_count": rated_count,
                "user_id": user_id,
            },
        )
        db.add(notif)

    logger.info(
        f"[ImprovementEngine] Notified {len(contributors)} users about "
        f"skill '{skill.name}' improvement triggered by their feedback"
    )
