"""Ein Tagesplan-Block muss zwischen erfolgreich, gescheitert und unauffindbar
unterscheiden — nicht jeden Endzustand als "done" melden (Issue #733).

Belegt am 12.09.2026: fuenf Bloecke standen auf "done", obwohl mindestens drei
davon gescheitert oder mitten in der Arbeit abgebrochen waren. `_arm_plan_blocks`
schrieb `done` fuer JEDEN Endzustand der verknuepften Aufgabe — auch fuer
`failed`, `cancelled` und fuer eine nicht mehr auffindbare Aufgabe. Die
Morgen-/Abendplanung konnte den Blockstatus damit nicht benutzen, um zu
entscheiden, was nachgeplant werden muss; gestorbene Arbeit wurde still nicht
nachgeholt.

Diese Tests treiben ``_arm_plan_blocks`` gegen eine echte (In-Memory-) DB — auf
dem Stand VOR #733 waeren die "failed"-Faelle hier rot (sie erwarteten "done").
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import DateTime, select
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.types import TypeDecorator

from app.models.agent_plan_item import AgentPlanItem
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus
from app.services.scheduler_service import SchedulerService

UTC = timezone.utc


class _UTCDateTime(TypeDecorator):
    """SQLite verliert tzinfo beim Roundtrip — ohne das werden die aware-vs-aware
    Zeitvergleiche in _arm_plan_blocks zu TypeError. Reiner Test-DB-Artefakt."""

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
    async def set(self, key, value, nx=False, ex=None):
        return True

    async def get(self, key):
        return None

    async def eval(self, script, numkeys, key, arg):
        return 0


class ARunningBlockReflectsItsTasksRealOutcomeTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _arm(self):
        with patch("app.db.session.async_session_factory", self.Session):
            return await self.svc._arm_plan_blocks()

    async def _seed(self, task_status, item_id=1, task_id="t1"):
        async with self.Session() as db:
            db.add(Task(id=task_id, title="x", prompt="x", agent_id="agent-1", status=task_status))
            db.add(AgentPlanItem(
                id=item_id, agent_id="agent-1", plan_date=date.today(),
                title="Block", status="running", task_id=task_id,
            ))
            await db.commit()

    async def _item(self, item_id=1):
        async with self.Session() as db:
            return await db.get(AgentPlanItem, item_id)

    async def test_ein_abgeschlossener_lauf_wird_done(self):
        await self._seed(TaskStatus.COMPLETED)
        await self._arm()
        self.assertEqual((await self._item()).status, "done")

    async def test_ein_gescheiterter_lauf_wird_failed_nicht_done(self):
        """Der gemeldete Fehler: genau dieser Fall stand vorher auf 'done'."""
        await self._seed(TaskStatus.FAILED)
        await self._arm()
        self.assertEqual((await self._item()).status, "failed")

    async def test_ein_abgebrochener_lauf_wird_failed_nicht_done(self):
        await self._seed(TaskStatus.CANCELLED)
        await self._arm()
        self.assertEqual((await self._item()).status, "failed")

    async def test_eine_verschwundene_aufgabe_bleibt_planned_nicht_done(self):
        """task_id zeigt ins Leere (z. B. Aufraeumen) — kein Ergebnis bewertbar,
        die Planung muss es trotzdem SEHEN koennen statt es als erledigt
        abzuhaken."""
        async with self.Session() as db:
            db.add(AgentPlanItem(
                id=1, agent_id="agent-1", plan_date=date.today(),
                title="Block", status="running", task_id="verschwunden",
            ))
            await db.commit()
        await self._arm()
        item = await self._item()
        self.assertEqual(item.status, "planned")
        self.assertIsNone(item.task_id)

    async def test_ohne_task_id_bleibt_es_beim_alten_verhalten_done(self):
        """Kein task_id war noch nie zugeordnet — anderer Fall als 'verschwunden',
        bewusst unveraendert gelassen."""
        async with self.Session() as db:
            db.add(AgentPlanItem(
                id=1, agent_id="agent-1", plan_date=date.today(),
                title="Block", status="running", task_id=None,
            ))
            await db.commit()
        await self._arm()
        self.assertEqual((await self._item()).status, "done")


class AMissedBlockReflectsWhatTheTitleSearchFoundTests(unittest.IsolatedAsyncioTestCase):
    """Der Nachzieh-Zweig fuer Bloecke, deren Zeitplan schon gefeuert hat, ohne
    dass die Aufgabe direkt verknuepft wurde — sucht den Task ueber den Titel."""

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

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _arm(self):
        with patch("app.db.session.async_session_factory", self.Session):
            return await self.svc._arm_plan_blocks()

    async def _item(self, item_id=1):
        async with self.Session() as db:
            return await db.get(AgentPlanItem, item_id)

    async def _seed_missed(self, task_status=None):
        async with self.Session() as db:
            db.add(Schedule(
                id="s1", name="Taegliches Ding", prompt="x", interval_seconds=0,
                agent_id="agent-1", enabled=True,
                next_run_at=datetime.now(UTC) + timedelta(days=1),
                last_run_at=datetime.now(UTC) - timedelta(minutes=5),
            ))
            db.add(AgentPlanItem(
                id=1, agent_id="agent-1", plan_date=date.today(),
                title="Block", status="planned", schedule_id="s1",
            ))
            if task_status is not None:
                db.add(Task(id="t1", title="[Plan] Taegliches Ding — heute", prompt="x",
                            agent_id="agent-1", status=task_status))
            await db.commit()

    async def test_ein_gefundener_gescheiterter_lauf_wird_failed(self):
        await self._seed_missed(TaskStatus.FAILED)
        await self._arm()
        self.assertEqual((await self._item()).status, "failed")

    async def test_ein_gefundener_abgeschlossener_lauf_wird_done(self):
        await self._seed_missed(TaskStatus.COMPLETED)
        await self._arm()
        self.assertEqual((await self._item()).status, "done")

    async def test_keine_auffindbare_aufgabe_bleibt_planned_nicht_done(self):
        await self._seed_missed(task_status=None)
        await self._arm()
        self.assertEqual((await self._item()).status, "planned")


if __name__ == "__main__":
    unittest.main()
