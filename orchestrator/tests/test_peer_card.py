"""Tests for the Honcho-inspired peer card."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.memory import AgentMemory
from app.models.agent import Agent
from app.models.user import User
from app.models.user_profile import UserProfile
from app.services.profile_extractor import (
    PEER_CARD_MAX_CHARS,
    extract_peer_card,
    render_peer_card,
)


_TABLES = [
    Base.metadata.tables["users"],
    Base.metadata.tables["agents"],
    Base.metadata.tables["agent_memories"],
    Base.metadata.tables["user_profiles"],
]


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
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _mk_user(db, user_id="u1"):
    u = User(id=user_id, email="t@t", name="Tester")
    db.add(u)
    return u


def _mk_agent(db, agent_id, user_id="u1"):
    a = Agent(id=agent_id, user_id=user_id, name=agent_id)
    db.add(a)
    return a


def _mk_mem(db, agent_id, *, content, importance=4, confidence=1.0, category="preference"):
    m = AgentMemory(
        agent_id=agent_id,
        category=category,
        key="k",
        content=content,
        importance=importance,
        confidence=confidence,
        tag_type="permanent",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(m)
    return m


@pytest.mark.asyncio
async def test_peer_card_aggregates_across_agents(db: AsyncSession):
    _mk_user(db)
    _mk_agent(db, "a1")
    _mk_agent(db, "a2")
    _mk_mem(db, "a1", content="Daniel prefers concise German responses.")
    _mk_mem(db, "a2", content="Daniel deploys to Hetzner Frankfurt for EU/DSGVO.")
    await db.commit()

    profile = await extract_peer_card(db, "u1")
    facts = profile.peer_card["facts"]
    assert len(facts) == 2
    assert profile.peer_card["chars"] <= PEER_CARD_MAX_CHARS


@pytest.mark.asyncio
async def test_low_confidence_excluded(db: AsyncSession):
    _mk_user(db)
    _mk_agent(db, "a1")
    _mk_mem(db, "a1", content="Maybe?", confidence=0.3)  # below 0.8
    await db.commit()

    profile = await extract_peer_card(db, "u1")
    assert profile.peer_card["facts"] == []


@pytest.mark.asyncio
async def test_dedup_credits_extra_agents(db: AsyncSession):
    _mk_user(db)
    _mk_agent(db, "a1")
    _mk_agent(db, "a2")
    shared = "Daniel verlangt Voice-Summary nach Code-Reviews vor dem Push."
    _mk_mem(db, "a1", content=shared)
    _mk_mem(db, "a2", content=shared)
    await db.commit()

    profile = await extract_peer_card(db, "u1")
    facts = profile.peer_card["facts"]
    assert len(facts) == 1
    assert set(facts[0]["agents"]) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_evicted_memories_are_excluded(db: AsyncSession):
    _mk_user(db)
    _mk_agent(db, "a1")
    evicted = _mk_mem(db, "a1", content="Old preference that was evicted.")
    evicted.evicted_at = datetime.now(timezone.utc)
    await db.commit()

    profile = await extract_peer_card(db, "u1")
    assert profile.peer_card["facts"] == []


@pytest.mark.asyncio
async def test_hard_cap_enforced(db: AsyncSession):
    _mk_user(db)
    _mk_agent(db, "a1")
    # 30 facts × ~120 chars > 2200 — only the most important should fit.
    for i in range(30):
        _mk_mem(db, "a1", content=f"Important fact #{i:03d}: " + "x" * 100,
                importance=5 if i < 5 else 3)
    await db.commit()

    profile = await extract_peer_card(db, "u1")
    assert profile.peer_card["chars"] <= PEER_CARD_MAX_CHARS
    # The highest-importance facts must be present.
    texts = [f["text"] for f in profile.peer_card["facts"]]
    assert any("#000" in t for t in texts)


@pytest.mark.asyncio
async def test_render_produces_markdown(db: AsyncSession):
    _mk_user(db)
    _mk_agent(db, "a1")
    _mk_mem(db, "a1", content="Daniel speaks German with the assistant.")
    await db.commit()

    profile = await extract_peer_card(db, "u1")
    rendered = render_peer_card(profile)
    assert "Shared User Profile" in rendered
    assert "Daniel speaks German" in rendered
    assert "a1" in rendered


@pytest.mark.asyncio
async def test_empty_profile_renders_empty_string(db: AsyncSession):
    _mk_user(db)
    profile = UserProfile(user_id="u1", dimensions={}, peer_card=None)
    db.add(profile)
    await db.commit()
    assert render_peer_card(profile) == ""
