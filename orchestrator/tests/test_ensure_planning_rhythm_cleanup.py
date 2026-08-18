"""Regression test: an old-style day-planning schedule (pre-Rhythmus naming)
was never cleaned up because the legacy filter only matched schedules ending
in "— Tagesplanung". A second, older naming scheme — "[Plan] Morgencheck: ..."
/ "[Plan] Abendplanung: ..." (with a date baked into the title) — slipped
through untouched.

Live incident (2026-08-18, "DEV_Prod Agent - Kerstin Alisch", stopped): this
exact duplicate fired every ~30s for weeks, always skipped because the agent
was stopped, producing pure log noise on top of the actual [Rhythmus]
schedules that already cover the same job.
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import DateTime, select
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.types import TypeDecorator

from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService

UTC = timezone.utc


class _UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo on round-trip; without this, aware-vs-naive
    datetime comparisons in scheduler_service raise TypeError — a test-DB
    artifact, not a real bug (mirrors test_arm_plan_blocks_race.py)."""

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


SQLiteDialect_pysqlite.colspecs = {
    **SQLiteDialect_pysqlite.colspecs,
    DateTime: _UTCDateTime,
}


class EnsurePlanningRhythmCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Schedule):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        from app.services.redis_service import RedisService
        self.svc = SchedulerService(redis=RedisService(redis_url="redis://fake"))

        now = datetime.now(UTC)
        async with self.Session() as db:
            db.add(Agent(id="agent-kerstin", name="Kerstin", state=AgentState.STOPPED, config={}))
            db.add(Schedule(
                id="proactive-kerstin", name="[Proactive] Kerstin", prompt="x",
                interval_seconds=3600, agent_id="agent-kerstin",
                next_run_at=now, enabled=True,
            ))
            # The two legacy naming schemes that must both be swept away.
            db.add(Schedule(
                id="legacy-morning", name="[Plan] Morgencheck: offene Aufgaben und neue Vorgaben sichten",
                prompt="x", interval_seconds=3600, agent_id="agent-kerstin",
                next_run_at=now - timedelta(days=1), enabled=True,
            ))
            db.add(Schedule(
                id="legacy-evening", name=f"[Plan] Abendplanung: {date.today()} vorbereiten",
                prompt="x", interval_seconds=3600, agent_id="agent-kerstin",
                next_run_at=now - timedelta(days=1), enabled=True,
            ))
            # A genuine, unrelated user plan-block must survive — the fix must
            # not turn into a blanket "[Plan] %" wipe.
            db.add(Schedule(
                id="real-plan-block", name="[Plan] Deploy-Gate Status pruefen",
                prompt="x", interval_seconds=3600, agent_id="agent-kerstin",
                next_run_at=now + timedelta(hours=1), enabled=True,
            ))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_both_legacy_naming_schemes_are_removed(self):
        with patch("app.db.session.async_session_factory", self.Session):
            created = await self.svc._ensure_planning_rhythm()

        async with self.Session() as db:
            names = {s.name for s in (await db.execute(select(Schedule))).scalars().all()}

        self.assertNotIn(
            "[Plan] Morgencheck: offene Aufgaben und neue Vorgaben sichten", names,
        )
        self.assertNotIn(f"[Plan] Abendplanung: {date.today()} vorbereiten", names)
        # Untouched: a real, unrelated plan block with the same "[Plan]" prefix.
        self.assertIn("[Plan] Deploy-Gate Status pruefen", names)
        # Replaced by the two real Rhythmus schedules.
        self.assertEqual(created, 2)
        self.assertTrue(any(n.startswith("[Rhythmus]") for n in names))


if __name__ == "__main__":
    unittest.main()
