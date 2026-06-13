"""Memory caps — Hermes-inspired hard limits on memory bucket size.

Each (agent_id, room, category) bucket has a CHAR_BUDGET. When a new
memory is saved and the bucket exceeds the budget, the LOWEST-IMPORTANCE
non-pinned memories are EVICTED (marked `evicted_at=now`) until the
bucket fits.

Eviction is distinct from supersession:
  * superseded_by points at a *replacement* row.
  * evicted_at means the row was dropped without replacement.
Every active-memory query MUST filter both:
  `superseded_by IS NULL AND evicted_at IS NULL`

Pinned memories (confidence >= 1.5 OR importance == 5) are exempt.

This mirrors how Hermes hard-caps USER.md (1375 chars) and MEMORY.md
(2200 chars) — except instead of editing in place, we use a dedicated
eviction marker so nothing is ever lost from the audit trail.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import AgentMemory

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_CHARS = 5000
PER_CATEGORY_BUDGET = {
    "preference": 1500,
    "fact": 2000,
    "procedure": 4000,
    "project": 3000,
    "decision": 3000,
    "learning": 5000,
    "contact": 2000,
}


def budget_for(category: str | None) -> int:
    if not category:
        return DEFAULT_BUDGET_CHARS
    return PER_CATEGORY_BUDGET.get(category, DEFAULT_BUDGET_CHARS)


def _is_pinned(m: AgentMemory) -> bool:
    return (m.confidence is not None and m.confidence >= 1.5) or m.importance >= 5


async def enforce(
    db: AsyncSession,
    *,
    agent_id: str,
    room: str | None,
    category: str | None,
) -> list[int]:
    """Supersede the lowest-importance memories until the bucket fits.

    Returns the list of superseded memory IDs (empty when no eviction needed).
    Caller should `await db.commit()` afterwards.
    """
    budget = budget_for(category)

    result = await db.execute(
        select(AgentMemory)
        .where(
            and_(
                AgentMemory.agent_id == agent_id,
                AgentMemory.category == category if category else True,
                AgentMemory.room == room if room is not None else AgentMemory.room.is_(None),
                AgentMemory.superseded_by.is_(None),
                AgentMemory.evicted_at.is_(None),
            )
        )
        .order_by(AgentMemory.importance.asc(), AgentMemory.created_at.asc())
    )
    items: list[AgentMemory] = list(result.scalars().all())
    total_chars = sum(len(m.content or "") for m in items)
    if total_chars <= budget:
        return []

    evicted: list[int] = []
    now = datetime.now(timezone.utc)
    # Walk lowest-importance / oldest first; keep pinned items.
    for m in items:
        if total_chars <= budget:
            break
        if _is_pinned(m):
            continue
        m.evicted_at = now
        total_chars -= len(m.content or "")
        evicted.append(m.id)

    if evicted:
        logger.info(
            "[MemoryCaps] evicted %d items in (%s, %s, %s) — over budget %d",
            len(evicted), agent_id, room, category, budget,
        )
    return evicted


async def bucket_usage(
    db: AsyncSession, agent_id: str, room: str | None, category: str | None
) -> dict:
    """Return current usage stats for one bucket — useful for the dashboard."""
    result = await db.execute(
        select(AgentMemory)
        .where(
            and_(
                AgentMemory.agent_id == agent_id,
                AgentMemory.category == category if category else True,
                AgentMemory.room == room if room is not None else AgentMemory.room.is_(None),
                AgentMemory.superseded_by.is_(None),
                AgentMemory.evicted_at.is_(None),
            )
        )
    )
    items = list(result.scalars().all())
    total = sum(len(m.content or "") for m in items)
    budget = budget_for(category)
    return {
        "agent_id": agent_id,
        "room": room,
        "category": category,
        "count": len(items),
        "chars": total,
        "budget": budget,
        "utilization": round(total / budget, 3) if budget else 0,
    }
