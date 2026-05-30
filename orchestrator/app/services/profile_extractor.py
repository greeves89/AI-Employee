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

# Honcho-style peer card: a compact cross-agent user snapshot.
PEER_CARD_MAX_CHARS = 2200          # matches Hermes MEMORY.md cap
PEER_CARD_MIN_CONFIDENCE = 0.8      # only "very confident" facts make it in
PEER_CARD_MIN_IMPORTANCE = 3        # filter out trivial entries


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

    # Refresh the peer card in the same pass so it never drifts behind
    # the dimensions. Caller will commit.
    try:
        await extract_peer_card(db, user_id)
    except Exception as e:
        logger.warning("peer_card refresh failed for user %s: %s", user_id, e)
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


async def extract_peer_card(db: AsyncSession, user_id: str) -> UserProfile:
    """Build a compact cross-agent peer card and persist it on the profile.

    Honcho-inspired: pulls high-confidence memories across ALL of the
    user's agents, deduplicates by content prefix, and packs the most
    important ones into a hard-capped 2200-char card. Inject the card
    into every agent's system prompt so they all share the same picture
    of the user.
    """
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
            AgentMemory.confidence >= PEER_CARD_MIN_CONFIDENCE,
            AgentMemory.importance >= PEER_CARD_MIN_IMPORTANCE,
        )
        .order_by(AgentMemory.importance.desc(), AgentMemory.updated_at.desc())
        .limit(300)
    )
    memories = list(result.scalars().all())

    seen_prefixes: set[str] = set()
    facts: list[dict] = []
    chars_used = 0
    for m in memories:
        text = (m.content or "").strip()
        if not text:
            continue
        # Dedupe near-duplicates by their first 80 chars.
        prefix = text[:80].lower()
        if prefix in seen_prefixes:
            # Same fact came from another agent — credit it but skip the dup.
            for f in facts:
                if f["text"].lower().startswith(prefix) and m.agent_id not in f["agents"]:
                    f["agents"].append(m.agent_id)
            continue
        # Hard cap: stop adding facts once we'd overflow.
        fact_len = len(text) + 4  # "- " + "\n" overhead
        if chars_used + fact_len > PEER_CARD_MAX_CHARS:
            break
        seen_prefixes.add(prefix)
        facts.append({
            "text": text,
            "confidence": float(m.confidence or 0.8),
            "agents": [m.agent_id],
            "category": m.category,
        })
        chars_used += fact_len

    profile.peer_card = {
        "facts": facts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "chars": chars_used,
        "max_chars": PEER_CARD_MAX_CHARS,
        "source_agent_count": len(agent_ids),
    }
    profile.peer_card_synced_at = datetime.now(timezone.utc)
    await db.commit()
    return profile


def render_peer_card(profile: UserProfile) -> str:
    """Render the peer card as markdown for system-prompt injection."""
    card = profile.peer_card if isinstance(profile.peer_card, dict) else None
    facts = (card or {}).get("facts") or []
    if not facts:
        return ""
    lines = ["## Shared User Profile (peer card)\n"]
    for f in facts:
        agents = ", ".join(f.get("agents", []))
        lines.append(f"- {f['text']}  _(via {agents})_")
    return "\n".join(lines)


def generate_profile_summary(profile: UserProfile) -> str:
    """Generate a natural-language summary for injection into agent system prompts."""
    if not profile.dimensions and not (profile.peer_card or {}).get("facts"):
        return ""

    sections: list[str] = []
    peer = render_peer_card(profile)
    if peer:
        sections.append(peer)

    if not profile.dimensions:
        return "\n\n".join(sections)

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

    if len(lines) > 1:
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
