"""Integration test for the autonomy hard-gate whitelist (C-1 regression).

`get_active_rules_for_agent` feeds the tool executor's whitelist. The executor
treats an EMPTY rule list as "no restrictions" (fail-open). This test proves that
a fine-tuned ("custom") agent — the level the matrix editor assigns as soon as a
single cell is changed — never yields an empty whitelist, but the exact set the
admin allowed.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.agent import Agent
from app.models.approval_rule import ApprovalRule
from app.models.autonomy_preset_rule import AutonomyPresetRule
from app.api.approval_rules import get_active_rules_for_agent


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    Base.metadata.tables["agents"],
                    Base.metadata.tables["approval_rules"],
                    Base.metadata.tables["autonomy_preset_rules"],
                ],
            )
        )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def _seed_l1_preset(db):
    db.add(AutonomyPresetRule(level="l1", name="l1-read", description="d",
                              category="file_read", sort_order=0))
    await db.commit()


def _cats(rules):
    return {r.category for r in rules}


@pytest.mark.asyncio
async def test_custom_matrix_yields_nonempty_whitelist(db):
    # L3 agent with one cell hardened (purchases → deny) → level "custom".
    matrix = {
        "file_read": "allow", "file_write": "allow", "shell_exec": "allow",
        "system_config": "allow", "web": "allow",
        "email_m365": "ask", "external_api": "ask", "messaging": "ask",
        "git_push": "ask", "purchases": "deny",
    }
    db.add(Agent(id="cust1234", name="A", autonomy_level="custom",
                 config={"autonomy_matrix": matrix}))
    await db.commit()

    rules = await get_active_rules_for_agent(db, "cust1234")
    cats = _cats(rules)
    assert rules, "custom agent must NOT get an empty (fail-open) whitelist"
    assert "shell_exec" in cats
    assert "purchase" not in cats   # the hardened cell is actually enforced
    assert "custom" not in cats     # all external caps were ask → gated


@pytest.mark.asyncio
async def test_custom_all_denied_still_nonempty(db):
    matrix = {k: "deny" for k in
              ("file_read", "file_write", "shell_exec", "system_config", "web",
               "email_m365", "external_api", "messaging", "git_push", "purchases")}
    db.add(Agent(id="deny5678", name="B", autonomy_level="custom",
                 config={"autonomy_matrix": matrix}))
    await db.commit()

    rules = await get_active_rules_for_agent(db, "deny5678")
    assert rules, "even all-deny must emit a sentinel rule to avoid fail-open"
    assert _cats(rules) == {"__none__"}   # nothing whitelisted, but not empty


@pytest.mark.asyncio
async def test_custom_without_matrix_falls_back_to_l1(db):
    await _seed_l1_preset(db)
    db.add(Agent(id="nomatrix", name="C", autonomy_level="custom", config={}))
    await db.commit()

    rules = await get_active_rules_for_agent(db, "nomatrix")
    assert rules, "missing matrix must fail CLOSED to L1, never empty"
    assert _cats(rules) == {"file_read"}


@pytest.mark.asyncio
async def test_unknown_level_string_not_fail_open(db):
    # The direct-injection variant: a bogus level string must not open everything.
    await _seed_l1_preset(db)
    db.add(Agent(id="bogus999", name="D", autonomy_level="hacker", config={}))
    await db.commit()

    rules = await get_active_rules_for_agent(db, "bogus999")
    assert rules, "unknown level must fail CLOSED, not empty"
    assert _cats(rules) == {"file_read"}
