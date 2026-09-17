"""Ein vor seiner Startzeit auf 'done' gesetzter Tagesplan-Block feuert
trotzdem, solange sein Einmal-Zeitplan aktiviert bleibt (Issue #748).

Belegt am 16.09.2026: drei im Morgencheck vorgezogene Bloecke per PATCH auf
`done` gesetzt — ihre `[Plan]`-Zeitplaene blieben `enabled=true`. `dropped`
wird korrekt behandelt (Zeitplan wird deaktiviert), nur `done` fiel durch, weil
die Annahme "done kommt nur NACH dem Lauf" fuer den PATCH-Pfad nicht gilt.

Zwei Ebenen abgesichert:
1. ``sync_block_schedule`` (der PATCH-Pfad selbst) deaktiviert den Zeitplan,
   sobald ``done`` gesetzt wird, WENN er noch nie gefeuert hat.
2. ``_execute_schedule`` (der Feuerpfad) prueft defensiv, ob der verknuepfte
   Block bereits ``done`` ist, und dispatcht in dem Fall keine Aufgabe.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.core.day_plan_store import sync_block_schedule
from app.models.agent_plan_item import AgentPlanItem
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

UTC = timezone.utc


class ADoneBlockThatNeverFiredDisablesItsScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (AgentPlanItem, Schedule):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, last_run_at=None):
        async with self.Session() as db:
            db.add(Schedule(
                id="s1", name="[Plan] Verifikation", prompt="x", interval_seconds=0,
                agent_id="agent-1", enabled=True,
                next_run_at=datetime.now(UTC) + timedelta(hours=2),
                last_run_at=last_run_at,
            ))
            db.add(AgentPlanItem(
                id=1, agent_id="agent-1", plan_date=datetime.now(UTC).date(),
                title="Verifikation", status="done",
                planned_start=datetime.now(UTC) + timedelta(hours=2),
                schedule_id="s1",
            ))
            await db.commit()
            row = await db.get(AgentPlanItem, 1)
            await sync_block_schedule(db, row)
            await db.commit()

    async def test_ein_nie_gefeuerter_zeitplan_wird_abgeschaltet(self):
        """Das war der gemeldete Fehler: genau dieser Fall blieb enabled=true."""
        await self._seed(last_run_at=None)
        async with self.Session() as db:
            schedule = await db.get(Schedule, "s1")
        self.assertFalse(schedule.enabled)

    async def test_ein_bereits_gefeuerter_zeitplan_bleibt_unveraendert(self):
        """Gegenprobe: 'gelaufen ist gelaufen' gilt weiterhin, wenn der
        Zeitplan schon einmal feuerte, bevor der Block done wurde."""
        await self._seed(last_run_at=datetime.now(UTC) - timedelta(hours=1))
        async with self.Session() as db:
            schedule = await db.get(Schedule, "s1")
        self.assertTrue(schedule.enabled)

    async def test_dropped_bleibt_unveraendert_deaktiviert(self):
        """Regression: die bestehende dropped-Behandlung darf nicht anders
        werden."""
        async with self.Session() as db:
            db.add(Schedule(
                id="s2", name="[Plan] Weg damit", prompt="x", interval_seconds=0,
                agent_id="agent-1", enabled=True,
                next_run_at=datetime.now(UTC) + timedelta(hours=2),
            ))
            db.add(AgentPlanItem(
                id=2, agent_id="agent-1", plan_date=datetime.now(UTC).date(),
                title="Weg damit", status="dropped",
                planned_start=datetime.now(UTC) + timedelta(hours=2),
                schedule_id="s2",
            ))
            await db.commit()
            row = await db.get(AgentPlanItem, 2)
            await sync_block_schedule(db, row)
            await db.commit()
            schedule = await db.get(Schedule, "s2")
        self.assertFalse(schedule.enabled)


class TheFiringPathRefusesAnAlreadyDoneBlockTests(unittest.IsolatedAsyncioTestCase):
    """Zweite Absicherung, falls ein direkter DB-Schreibvorgang die
    sync_block_schedule-Abschaltung umgeht — der Feuerpfad selbst darf einen
    'done'-Block nie dispatchen."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (AgentPlanItem, Schedule):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        from app.services.redis_service import RedisService
        redis = RedisService(redis_url="redis://fake")
        self.svc = SchedulerService(redis=redis)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_ein_bereits_erledigter_block_wird_nicht_erneut_dispatcht(self):
        async with self.Session() as db:
            db.add(Schedule(
                id="s1", name="[Plan] Verifikation", prompt="x", interval_seconds=0,
                agent_id=None, enabled=True, next_run_at=datetime.now(UTC),
            ))
            db.add(AgentPlanItem(
                id=1, agent_id="agent-1", plan_date=datetime.now(UTC).date(),
                title="Verifikation", status="done", schedule_id="s1",
            ))
            await db.commit()
            schedule = await db.get(Schedule, "s1")

            router = MagicMock()
            router.create_and_route_task = AsyncMock()
            await self.svc._execute_schedule(db, router, schedule, datetime.now(UTC))

        router.create_and_route_task.assert_not_awaited()
        self.assertFalse(schedule.enabled)

    async def test_ein_laufender_block_wird_ganz_normal_dispatcht(self):
        """Gegenprobe: der Schutz darf nur 'done' treffen, nicht jeden Block."""
        async with self.Session() as db:
            db.add(Schedule(
                id="s2", name="[Plan] Andere Sache", prompt="x", interval_seconds=0,
                agent_id=None, enabled=True, next_run_at=datetime.now(UTC),
            ))
            db.add(AgentPlanItem(
                id=2, agent_id="agent-1", plan_date=datetime.now(UTC).date(),
                title="Andere Sache", status="planned", schedule_id="s2",
            ))
            await db.commit()
            schedule = await db.get(Schedule, "s2")

            router = MagicMock()
            task = MagicMock(id="t1")
            router.create_and_route_task = AsyncMock(return_value=task)
            await self.svc._execute_schedule(db, router, schedule, datetime.now(UTC))

        router.create_and_route_task.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
