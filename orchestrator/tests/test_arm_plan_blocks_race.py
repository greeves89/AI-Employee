"""Regression test for the #548 recurrence on 2026-08-13: _arm_plan_blocks races.

The per-agent dispatch lock added in #561 guards the moment a schedule is about
to *fire* a task. It does NOT guard the earlier moment where a plan block turns
into a Schedule row in the first place: ``_arm_plan_blocks`` selects
``AgentPlanItem`` rows with ``schedule_id IS NULL`` and inserts a fresh
``Schedule`` for each. If two scheduler ticks (this process on an overlapping
tick, or a second orchestrator replica) run that select+insert concurrently,
both can see the item as unclaimed before either commits and both create a
Schedule row for it. That is exactly what happened for the "Deploy-Gate Status
pruefen" plan block on 2026-08-13: two independent [Plan] tasks fired for the
same block and sent contradicting Telegram updates to the user (see issue
#548, reopened).

This test reproduces the race against a real (in-memory) DB with two
concurrent ``_arm_plan_blocks()`` calls sharing one fake Redis client, and
proves that with the new ``arm_plan_blocks`` lock, at most one Schedule row is
created per orphaned plan item.
"""

import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import DateTime, select
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.types import TypeDecorator

from app.models.agent_plan_item import AgentPlanItem
from app.models.schedule import Schedule
from app.models.task import Task
from app.services.scheduler_service import SchedulerService

UTC = timezone.utc


class _UTCDateTime(TypeDecorator):
    """SQLite's DATETIME loses tzinfo on round-trip (unlike Postgres, which the
    ``DateTime(timezone=True)`` columns target in production). Without this,
    every timestamp read back from the in-memory DB comes back naive and
    ``_arm_plan_blocks``'s aware-vs-aware comparisons raise TypeError — a test
    DB artifact, not a real bug."""

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


class _FakeAsyncRedisClient:
    """Same SET NX / Lua-eval semantics as the real client, nothing else."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def set(self, key, value, nx=False, ex=None):
        async with self._lock:
            if nx and key in self._store:
                return None
            self._store[key] = value
            return True

    async def get(self, key):
        return self._store.get(key)

    async def eval(self, script, numkeys, key, arg):
        async with self._lock:
            if self._store.get(key) == arg:
                del self._store[key]
                return 1
            return 0


class ArmPlanBlocksRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (AgentPlanItem, Schedule, Task):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        from app.services.redis_service import RedisService
        redis = RedisService(redis_url="redis://fake")
        redis.client = _FakeAsyncRedisClient()
        self.svc = SchedulerService(redis=redis)

        async with self.Session() as db:
            db.add(AgentPlanItem(
                agent_id="agent-1",
                plan_date=date.today(),
                title="Deploy-Gate Status pruefen",
                planned_start=datetime.now(UTC) - timedelta(minutes=1),
                source="self",
                status="planned",
            ))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _arm_with_patched_session(self):
        with patch("app.db.session.async_session_factory", self.Session):
            return await self.svc._arm_plan_blocks()

    async def test_sequential_arming_is_idempotent(self):
        """Baseline: running it twice back-to-back must not double-create."""
        first = await self._arm_with_patched_session()
        second = await self._arm_with_patched_session()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

        async with self.Session() as db:
            rows = (await db.execute(select(Schedule))).scalars().all()
        self.assertEqual(len(rows), 1, "exactly one Schedule for the one plan item")

    async def test_concurrent_arming_creates_only_one_schedule(self):
        """Two overlapping _arm_plan_blocks() calls (the actual #548 race) must
        not both win: only one Schedule row may exist for the plan item
        afterwards, and the item must end up pointing at it."""
        await asyncio.gather(
            self._arm_with_patched_session(),
            self._arm_with_patched_session(),
        )

        async with self.Session() as db:
            schedules = (await db.execute(select(Schedule))).scalars().all()
            items = (await db.execute(select(AgentPlanItem))).scalars().all()

        self.assertEqual(
            len(schedules), 1,
            "two concurrent arm passes must not create two Schedule rows for "
            "the same plan block (this is what caused the duplicate [Plan] "
            "task dispatch and the contradicting Telegram messages in #548)",
        )
        self.assertEqual(items[0].schedule_id, schedules[0].id)


if __name__ == "__main__":
    unittest.main()
