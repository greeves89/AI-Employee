"""Tests for the Hermes-inspired memory char-budget caps."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.memory import AgentMemory
from app.services.memory_caps import (
    enforce,
    bucket_usage,
    PER_CATEGORY_BUDGET,
    DEFAULT_BUDGET_CHARS,
    budget_for,
)


_TABLES = [Base.metadata.tables["agent_memories"]]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_TABLES))

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _add(db, *, agent="a1", room="r1", category="preference", importance=3,
         confidence=1.0, content="x", created_offset=0):
    m = AgentMemory(
        agent_id=agent,
        category=category,
        key="k",
        content=content,
        importance=importance,
        confidence=confidence,
        room=room,
        tag_type="permanent",
        created_at=datetime.now(timezone.utc),
    )
    db.add(m)
    return m


def test_budget_for_known_and_unknown_categories():
    assert budget_for("preference") == PER_CATEGORY_BUDGET["preference"]
    assert budget_for(None) == DEFAULT_BUDGET_CHARS
    assert budget_for("nonsense") == DEFAULT_BUDGET_CHARS


@pytest.mark.asyncio
async def test_no_eviction_when_under_budget(db: AsyncSession):
    _add(db, content="a" * 100)
    await db.commit()
    evicted = await enforce(db, agent_id="a1", room="r1", category="preference")
    assert evicted == []


@pytest.mark.asyncio
async def test_evicts_lowest_importance_first(db: AsyncSession):
    # preference budget = 1500
    keep = _add(db, content="K" * 600, importance=4)
    drop = _add(db, content="D" * 1200, importance=1)
    await db.commit()

    evicted = await enforce(db, agent_id="a1", room="r1", category="preference")
    await db.commit()
    assert drop.id in evicted
    assert keep.id not in evicted


@pytest.mark.asyncio
async def test_pinned_memories_are_exempt(db: AsyncSession):
    pinned = _add(db, content="P" * 2000, importance=5)  # importance 5 = pinned
    await db.commit()

    evicted = await enforce(db, agent_id="a1", room="r1", category="preference")
    assert pinned.id not in evicted


@pytest.mark.asyncio
async def test_high_confidence_is_exempt(db: AsyncSession):
    pinned = _add(db, content="C" * 2000, importance=2, confidence=2.0)
    await db.commit()

    evicted = await enforce(db, agent_id="a1", room="r1", category="preference")
    assert pinned.id not in evicted


@pytest.mark.asyncio
async def test_bucket_usage_reports_correctly(db: AsyncSession):
    _add(db, content="x" * 200)
    _add(db, content="y" * 300)
    await db.commit()

    usage = await bucket_usage(db, "a1", "r1", "preference")
    assert usage["count"] == 2
    assert usage["chars"] == 500
    assert usage["budget"] == PER_CATEGORY_BUDGET["preference"]
    assert 0 < usage["utilization"] < 1


@pytest.mark.asyncio
async def test_eviction_marks_evicted_at(db: AsyncSession):
    drop = _add(db, content="D" * 1600, importance=1)
    await db.commit()

    evicted = await enforce(db, agent_id="a1", room="r1", category="preference")
    await db.commit()
    await db.refresh(drop)
    assert drop.id in evicted
    assert drop.evicted_at is not None
    # Eviction MUST NOT touch superseded_by — that would conflict with the
    # dedup audit chain. The evicted_at column is the eviction marker.
    assert drop.superseded_by is None
    assert drop.superseded_at is None


@pytest.mark.asyncio
async def test_already_evicted_items_dont_count_toward_budget(db: AsyncSession):
    """An evicted row must be invisible to subsequent enforce() calls."""
    old = _add(db, content="X" * 1600, importance=1)
    await db.commit()
    # First enforce: should evict the big low-importance row (over 1500 budget).
    await enforce(db, agent_id="a1", room="r1", category="preference")
    await db.commit()
    await db.refresh(old)
    assert old.evicted_at is not None

    # Now adding a fresh row should NOT see the evicted one in the count.
    new = _add(db, content="N" * 200, importance=3)
    await db.commit()
    usage = await bucket_usage(db, "a1", "r1", "preference")
    assert usage["chars"] == 200
    assert usage["count"] == 1
