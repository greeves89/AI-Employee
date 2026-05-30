"""Profile Extractor — builds adaptive user profiles from agent memories.

Scans preference/correction/learning memories per user and extracts
structured dimensions. Confidence decays over time for stale entries.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.memory import AgentMemory
from app.models.user_profile import UserProfile, UserProfileEvent

logger = logging.getLogger(__name__)

DIMENSION_KEYWORDS = {
    "communication": [
        "verbosity", "concise", "detailed", "tone", "informal", "formal",
        "emoji", "language", "german", "english", "antwort", "kurz", "lang",
    ],
    "technical": [
        "python", "typescript", "javascript", "react", "fastapi", "framework",
        "library", "testing", "test", "integration", "unit", "code", "style",
    ],
    "workflow": [
        "commit", "pr", "branch", "deploy", "review", "approval", "git",
        "convention", "process", "pipeline", "ci", "cd",
    ],
    "schedule": [
        "morning", "evening", "night", "hours", "time", "notification",
        "telegram", "reminder", "daily", "weekly",
    ],
}

CONFIDENCE_DECAY_RATE = 0.02


async def get_or_create_profile(db: AsyncSession, user_id: str) -> UserProfile:
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id, dimensions={}, profile_version=1)
        db.add(profile)
        await db.flush()
    return profile


async def _get_user_agents(db: AsyncSession, user_id: str) -> list[str]:
    result = await db.execute(
        select(Agent.id).where(Agent.user_id == user_id)
    )
    return [row[0] for row in result.all()]


def _classify_dimension(content: str) -> str | None:
    content_lower = content.lower()
    scores: dict[str, int] = {}
    for dim, keywords in DIMENSION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            scores[dim] = score
    if not scores:
        return None
    return max(scores, key=scores.get)


def _extract_key_value(category: str, key: str, content: str) -> tuple[str, str]:
    """Best-effort extraction of a preference key and value from memory content."""
    if ":" in content and len(content) < 500:
        parts = content.split(":", 1)
        if len(parts[0].strip().split()) <= 5:
            return parts[0].strip().lower().replace(" ", "_"), parts[1].strip()
    return key, content[:200]


async def extract_profile(db: AsyncSession, user_id: str) -> UserProfile:
    """Scan user's agent memories and build/update the adaptive profile."""
    profile = await get_or_create_profile(db, user_id)
    agent_ids = await _get_user_agents(db, user_id)

    if not agent_ids:
        return profile

    result = await db.execute(
        select(AgentMemory).where(
            AgentMemory.agent_id.in_(agent_ids),
            AgentMemory.category.in_(["preference", "learning", "correction", "decision"]),
            AgentMemory.superseded_by.is_(None),
            AgentMemory.evicted_at.is_(None),
        ).order_by(AgentMemory.updated_at.desc()).limit(200)
    )
    memories = result.scalars().all()

    if not memories:
        return profile

    dims: dict = dict(profile.dimensions) if profile.dimensions else {}
    events: list[UserProfileEvent] = []

    for mem in memories:
        dimension = _classify_dimension(mem.content)
        if dimension is None:
            continue

        pref_key, pref_value = _extract_key_value(mem.category, mem.key, mem.content)

        age_days = (datetime.now(timezone.utc) - mem.updated_at).days if mem.updated_at else 0
        confidence = max(0.1, mem.confidence - (age_days * CONFIDENCE_DECAY_RATE))

        if dimension not in dims:
            dims[dimension] = {}

        existing = dims[dimension].get(pref_key)
        existing_conf = existing.get("confidence", 0) if isinstance(existing, dict) else 0

        if confidence > existing_conf:
            old_val = existing.get("value") if isinstance(existing, dict) else None
            dims[dimension][pref_key] = {
                "value": pref_value,
                "confidence": round(confidence, 2),
                "source_memory_id": mem.id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if old_val != pref_value:
                events.append(UserProfileEvent(
                    user_id=user_id,
                    dimension=dimension,
                    key=pref_key,
                    old_value=str(old_val) if old_val else None,
                    new_value=pref_value[:500],
                    source="extraction",
                    confidence=round(confidence, 2),
                ))

    profile.dimensions = dims
    profile.profile_version += 1
    profile.last_extracted_at = datetime.now(timezone.utc)

    for ev in events:
        db.add(ev)

    await db.flush()
    logger.info("Extracted profile for user %s: v%d, %d dimensions, %d events",
                user_id, profile.profile_version, len(dims), len(events))
    return profile


async def update_dimension(
    db: AsyncSession,
    user_id: str,
    dimension: str,
    key: str,
    value: str,
    confidence: float = 1.0,
) -> UserProfile:
    """Manually set a specific dimension key — user corrections get confidence=1.0."""
    profile = await get_or_create_profile(db, user_id)
    dims: dict = dict(profile.dimensions) if profile.dimensions else {}

    if dimension not in dims:
        dims[dimension] = {}

    old_entry = dims[dimension].get(key)
    old_val = old_entry.get("value") if isinstance(old_entry, dict) else None

    dims[dimension][key] = {
        "value": value,
        "confidence": round(confidence, 2),
        "source_memory_id": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    profile.dimensions = dims
    profile.profile_version += 1

    db.add(UserProfileEvent(
        user_id=user_id,
        dimension=dimension,
        key=key,
        old_value=str(old_val) if old_val else None,
        new_value=value[:500],
        source="manual",
        confidence=round(confidence, 2),
    ))

    await db.flush()
    return profile


async def delete_dimension(
    db: AsyncSession, user_id: str, dimension: str, key: str | None = None
) -> UserProfile:
    """Remove a dimension or a specific key within a dimension."""
    profile = await get_or_create_profile(db, user_id)
    dims: dict = dict(profile.dimensions) if profile.dimensions else {}

    if dimension not in dims:
        return profile

    if key is None:
        del dims[dimension]
    elif key in dims[dimension]:
        del dims[dimension][key]
        if not dims[dimension]:
            del dims[dimension]

    profile.dimensions = dims
    profile.profile_version += 1
    await db.flush()
    return profile


def generate_profile_summary(profile: UserProfile) -> str:
    """Generate a natural-language summary for injection into agent system prompts."""
    if not profile.dimensions:
        return ""

    lines = ["## User Profile (auto-learned preferences)\n"]
    for dim_name, entries in profile.dimensions.items():
        if not isinstance(entries, dict):
            continue
        high_conf = {
            k: v for k, v in entries.items()
            if isinstance(v, dict) and v.get("confidence", 0) >= 0.5
        }
        if not high_conf:
            continue
        lines.append(f"### {dim_name.title()}")
        for k, v in high_conf.items():
            val = v.get("value", "")
            conf = v.get("confidence", 0)
            if len(val) > 100:
                val = val[:100] + "..."
            lines.append(f"- **{k}**: {val} (confidence: {conf})")
        lines.append("")

    return "\n".join(lines) if len(lines) > 1 else ""
