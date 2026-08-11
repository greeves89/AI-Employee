"""Issue #547: collect_preload gets a task-conditioned, evidence-backed slice.

The static preload (importance>=5, recent learnings, ...) doesn't know what the
current task is about. These tests cover the pure re-ranking/dedup helper
(``_rank_task_relevant``) without a DB, and ``collect_preload`` end-to-end with a
stubbed AsyncSession so no Postgres/pgvector is needed.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.memory_preload import (
    TASK_RELEVANT_LIMIT,
    _rank_task_relevant,
    collect_preload,
)


def _row(id_, similarity, importance=3, room=None, days_old=1):
    now = datetime.now(timezone.utc)
    return {
        "id": id_,
        "category": "learning",
        "key": f"mem-{id_}",
        "content": f"content-{id_}",
        "importance": importance,
        "access_count": 0,
        "created_at": now - timedelta(days=days_old),
        "room": room,
        "tag_type": "permanent",
        "last_accessed_at": None,
        "similarity": similarity,
    }


def test_rank_orders_by_score_descending():
    rows = [_row(1, similarity=0.5), _row(2, similarity=0.95), _row(3, similarity=0.7)]
    out = _rank_task_relevant(rows, seen=set(), query_room=None)
    assert [m["key"] for m in out] == ["mem-2", "mem-3", "mem-1"]


def test_rank_skips_already_seen_ids():
    rows = [_row(1, similarity=0.9), _row(2, similarity=0.8)]
    out = _rank_task_relevant(rows, seen={1}, query_room=None)
    assert [m["key"] for m in out] == ["mem-2"]


def test_rank_caps_to_limit():
    rows = [_row(i, similarity=0.5 + i * 0.001) for i in range(TASK_RELEVANT_LIMIT + 10)]
    out = _rank_task_relevant(rows, seen=set(), query_room=None)
    assert len(out) == TASK_RELEVANT_LIMIT


def test_rank_entry_carries_evidence_source():
    rows = [_row(42, similarity=0.9)]
    out = _rank_task_relevant(rows, seen=set(), query_room=None)
    assert out[0]["source"] == "memory:42"


def test_rank_adds_returned_ids_to_seen():
    seen: set = set()
    rows = [_row(7, similarity=0.9)]
    _rank_task_relevant(rows, seen, query_room=None)
    assert 7 in seen


def _db_with(high_imp=None, creds=None, learnings=None):
    db = MagicMock()
    queue = [high_imp or [], [], creds or [], learnings or []]

    async def execute(stmt, *a, **kw):
        result = MagicMock()
        items = queue.pop(0) if queue else []
        result.scalars.return_value.all.return_value = items
        return result

    db.execute = AsyncMock(side_effect=execute)
    return db


@pytest.mark.asyncio
async def test_task_relevant_empty_without_task_context():
    db = _db_with()
    out = await collect_preload(db, "agent-1")
    assert out["task_relevant"] == []


@pytest.mark.asyncio
async def test_task_relevant_empty_when_embeddings_disabled():
    db = _db_with()
    fake_svc = MagicMock(enabled=False)
    with patch("app.services.embedding_service.get_embedding_service", return_value=fake_svc):
        out = await collect_preload(db, "agent-1", task_context="fix the login bug")
    assert out["task_relevant"] == []


@pytest.mark.asyncio
async def test_task_relevant_never_raises_on_embedding_failure():
    db = _db_with()
    fake_svc = MagicMock(enabled=True)
    fake_svc.embed = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("app.services.embedding_service.get_embedding_service", return_value=fake_svc):
        out = await collect_preload(db, "agent-1", task_context="fix the login bug")
    assert out["task_relevant"] == []
    # Static buckets must still come back — a broken embedding call must not
    # take down the whole preload.
    assert "critical" in out and "credentials" in out and "recent_learnings" in out
