"""Memory scoring — multi-strategy ranking for semantic search.

Issue #24: instead of ranking memories purely by cosine similarity, we
combine 4 signals with tuned weights:

    final_score = 0.50 * semantic
                + 0.30 * structural   (room-hierarchy match)
                + 0.15 * recency      (hybrid exp/log decay + access_boost)
                + 0.05 * importance   (user-assigned 1..5)

The caller gets top-K after re-ranking. Fetching strategy: pull 4x K
candidates by semantic, then re-rank locally.

All inputs are normalized to [0..1] before weighting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.memory_key_schema import TAG_TYPE_PERMANENT, TAG_TYPE_TRANSIENT


# Weights — easy to A/B test later.
W_SEMANTIC = 0.50
W_STRUCTURAL = 0.30
W_RECENCY = 0.15
W_IMPORTANCE = 0.05


@dataclass
class ScoringInputs:
    """Per-memory signals used for re-ranking."""

    semantic_sim: float  # [0..1] cosine similarity from pgvector
    query_room: str | None
    memory_room: str | None
    memory_tag_type: str  # "transient" | "permanent"
    last_accessed_at: datetime | None
    created_at: datetime
    access_count: int
    importance: int  # 1..5


def structural_score(query_room: str | None, memory_room: str | None) -> float:
    """Room-hierarchy match score.

    exact match   -> 1.00
    sub-room      -> 0.70  (query is under memory_room)
    parent-room   -> 0.50  (memory is under query_room)
    cousin / unrelated -> 0.20
    no query_room -> 0.50  (neutral)
    """
    if not query_room:
        return 0.5
    if not memory_room:
        return 0.2
    if memory_room == query_room:
        return 1.0
    if memory_room.startswith(query_room + "/"):
        return 0.7  # memory is a sub-room of the query
    if query_room.startswith(memory_room + "/"):
        return 0.5  # memory is a parent-room of the query
    # Check common prefix (cousins)
    q_parts = query_room.split("/")
    m_parts = memory_room.split("/")
    common = 0
    for qp, mp in zip(q_parts, m_parts):
        if qp == mp:
            common += 1
        else:
            break
    if common > 0:
        return 0.3
    return 0.2


def recency_score(
    *,
    tag_type: str,
    last_accessed_at: datetime | None,
    created_at: datetime,
    access_count: int,
    now: datetime | None = None,
) -> float:
    """Hybrid exp/log decay + access boost.

    Transient memories fall off fast (exp over 30 days).
    Permanent memories decay gently (log, preserves old info).
    Access boost bumps both: memories that keep being referenced stay
    relevant longer.
    """
    now = now or datetime.now(timezone.utc)
    ref = last_accessed_at or created_at
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    t_days = max(0.0, (now - ref).total_seconds() / 86400.0)

    # Access boost: +0.1 per access, capped at +1.0.
    access_boost = min(1.0, 0.1 * float(access_count))

    if tag_type == TAG_TYPE_TRANSIENT:
        base = math.exp(-t_days / 30.0)
    else:  # permanent, or default
        base = 1.0 / (1.0 + math.log1p(t_days / 7.0))

    return min(1.0, base + 0.2 * access_boost)


def importance_score(importance: int) -> float:
    """Importance is 1..5, normalize to [0.2..1.0]."""
    clamped = max(1, min(5, int(importance)))
    return clamped / 5.0


def final_score(inp: ScoringInputs, now: datetime | None = None) -> float:
    """Return the weighted combined score in [0..1]."""
    sem = max(0.0, min(1.0, inp.semantic_sim))
    struct = structural_score(inp.query_room, inp.memory_room)
    rec = recency_score(
        tag_type=inp.memory_tag_type,
        last_accessed_at=inp.last_accessed_at,
        created_at=inp.created_at,
        access_count=inp.access_count,
        now=now,
    )
    imp = importance_score(inp.importance)

    return (
        W_SEMANTIC * sem
        + W_STRUCTURAL * struct
        + W_RECENCY * rec
        + W_IMPORTANCE * imp
    )
