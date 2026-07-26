"""Reciprocal Rank Fusion (RRF) for hybrid retrieval.

Pure, dependency-free, unit-testable. Fuses several ranked result lists
(e.g. semantic/vector search + keyword/BM25 search) into one ranking without
needing the individual scores to be on a comparable scale — the standard
"Advanced RAG" fusion step.

RRF score for an item = sum over each list of 1 / (k + rank), where rank is the
0-based position of the item in that list. k (default 60) damps the influence
of very high ranks; it is the value from the original Cormack et al. paper.
"""
from __future__ import annotations

from collections.abc import Hashable, Sequence

DEFAULT_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[Hashable]],
    *,
    k: int = DEFAULT_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[Hashable, float]]:
    """Fuse ranked lists of item keys into one ``(key, score)`` ranking.

    Args:
        ranked_lists: each inner sequence is a list of item keys ordered best
            -> worst. Keys must be hashable and comparable across lists (use the
            same id for the same item in every list).
        k: RRF damping constant (default 60).
        weights: optional per-list weight (e.g. trust semantic more than
            keyword). Defaults to 1.0 for every list.

    Returns:
        ``(key, fused_score)`` tuples sorted by descending score. Ties are
        broken deterministically by the key's first-seen order.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights length must match number of ranked_lists")

    scores: dict[Hashable, float] = {}
    first_seen: dict[Hashable, int] = {}
    order = 0
    for lst, weight in zip(ranked_lists, weights):
        for rank, key in enumerate(lst):
            scores[key] = scores.get(key, 0.0) + weight * (1.0 / (k + rank))
            if key not in first_seen:
                first_seen[key] = order
                order += 1

    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
