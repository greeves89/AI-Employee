"""Aktivitaets-Zeitachse (HANDOVER.md Schritt 2): eine Tagesleiste pro Agent mit
geplanten Terminen (aus Zeitplaenen) und tatsaechlichen Laeufen (aus Aufgaben).

Gegen ECHTES SQL (in-memory SQLite), nicht gegen einen Mock — die Sichtbarkeits-
und Ueberlappungs-Filterung passiert in der Query; ein Fake-Stub wuerde genau
den Schutz/die Logik wegtesten, die hier getestet werden soll (vgl.
test_app_sharing.py, gleiches Muster).
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.activity import get_activity_timeline
from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus

ADMIN = SimpleNamespace(id="admin-1", role="admin")
OWNER = SimpleNamespace(id="user-owner", role=None)
STRANGER = SimpleNamespace(id="user-stranger", role=None)

DAY_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
DAY_END = datetime(2026, 8, 2, tzinfo=timezone.utc)


def _agent(agent_id, name, user_id=OWNER.id):
    return Agent(id=agent_id, name=name, user_id=user_id, config={})


def _task(agent_id, started, completed, status=TaskStatus.COMPLETED, title="x"):
    return Task(
        id=f"t_{uuid.uuid4().hex[:10]}", title=title, prompt="p", agent_id=agent_id,
        status=status, started_at=started, completed_at=completed,
    )


def _schedule(agent_id, cron_expression=None, interval_seconds=0, next_run_at=None, enabled=True):
    return Schedule(
        id=f"s_{uuid.uuid4().hex[:10]}", name="[Proactive] x", prompt="p", agent_id=agent_id,
        cron_expression=cron_expression, interval_seconds=interval_seconds,
        next_run_at=next_run_at or DAY_START, enabled=enabled,
    )


class ActivityTimelineTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                Agent.metadata.create_all,
                tables=[Agent.__table__, Task.__table__, Schedule.__table__, AgentAccess.__table__],
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def add(self, *rows):
        for r in rows:
            self.db.add(r)
        await self.db.commit()


class RangeValidationTests(ActivityTimelineTestBase):
    async def test_end_before_start_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await get_activity_timeline(start=DAY_END, end=DAY_START, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_naive_datetimes_are_treated_as_utc_not_rejected_or_misread(self):
        """Ein Query-Parameter ohne Offset ist mehrdeutig — statt sich auf das
        Verhalten des DB-Treibers zu verlassen, wird UTC angenommen."""
        naive_start = datetime(2026, 8, 1)
        naive_end = datetime(2026, 8, 2)
        await self.add(_agent("a1", "Agent One"))
        await self.add(_task("a1", DAY_START + timedelta(hours=1), DAY_START + timedelta(hours=2)))
        result = await get_activity_timeline(start=naive_start, end=naive_end, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(len(result["agents"][0]["tasks"]), 1)
        self.assertTrue(result["start"].endswith("+00:00") or result["start"].endswith("Z"))

    async def test_excessively_wide_range_is_rejected(self):
        """schedule_occurrences() laeuft synchron (kein await) im async Handler —
        ein unbegrenzter Zeitraum mal unbegrenzt viele Zeitplaene koennte den
        Event-Loop fuer ALLE gleichzeitigen Requests blockieren, nicht nur den
        eigenen. Die UI fragt ohnehin nie mehr als einen Tag an."""
        with self.assertRaises(HTTPException) as ctx:
            await get_activity_timeline(
                start=datetime(1, 1, 1, tzinfo=timezone.utc),
                end=datetime(9999, 1, 1, tzinfo=timezone.utc),
                agent_id=None, user=ADMIN, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_a_reasonable_multi_month_range_still_works(self):
        await self.add(_agent("a1", "Agent One"))
        result = await get_activity_timeline(
            start=DAY_START, end=DAY_START + timedelta(days=90),
            agent_id=None, user=ADMIN, db=self.db,
        )
        self.assertEqual(len(result["agents"]), 1)


class TaskBarOverlapTests(ActivityTimelineTestBase):
    async def test_task_started_and_finished_inside_the_day_is_included(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_task("a1", DAY_START + timedelta(hours=1), DAY_START + timedelta(hours=2)))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(len(result["agents"][0]["tasks"]), 1)

    async def test_task_still_running_with_no_completed_at_is_included(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_task("a1", DAY_START + timedelta(hours=1), None, status=TaskStatus.RUNNING))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        bars = result["agents"][0]["tasks"]
        self.assertEqual(len(bars), 1)
        self.assertIsNone(bars[0]["completed_at"])

    async def test_task_that_started_yesterday_and_finished_today_overlaps(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_task("a1", DAY_START - timedelta(hours=2), DAY_START + timedelta(hours=1)))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(len(result["agents"][0]["tasks"]), 1)

    async def test_task_entirely_before_the_range_is_excluded(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_task("a1", DAY_START - timedelta(hours=5), DAY_START - timedelta(hours=4)))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(result["agents"][0]["tasks"], [])

    async def test_task_entirely_after_the_range_is_excluded(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_task("a1", DAY_END + timedelta(hours=1), DAY_END + timedelta(hours=2)))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(result["agents"][0]["tasks"], [])

    async def test_never_started_task_is_excluded(self):
        """Pending/queued Aufgaben ohne started_at gehoeren nicht auf die Zeitleiste
        (die zeigt was lief/laeuft, nicht die Warteschlange)."""
        await self.add(_agent("a1", "Agent One"))
        await self.add(Task(id="t1", title="x", prompt="p", agent_id="a1", status=TaskStatus.PENDING))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(result["agents"][0]["tasks"], [])


class ScheduleMarkTests(ActivityTimelineTestBase):
    async def test_cron_schedule_produces_marks_for_the_day(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_schedule("a1", cron_expression="0 6,18 * * *"))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(len(result["agents"][0]["scheduled_marks"]), 2)

    async def test_disabled_schedule_produces_no_marks(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_schedule("a1", cron_expression="0 6 * * *", enabled=False))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(result["agents"][0]["scheduled_marks"], [])

    async def test_interval_schedule_produces_marks(self):
        await self.add(_agent("a1", "Agent One"))
        await self.add(_schedule("a1", interval_seconds=3600 * 6, next_run_at=DAY_START))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual(len(result["agents"][0]["scheduled_marks"]), 4)


class OwnershipScopingTests(ActivityTimelineTestBase):
    async def test_admin_sees_every_agent(self):
        await self.add(_agent("a1", "Mine", user_id=OWNER.id), _agent("a2", "Someone Else's", user_id="other-user"))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        self.assertEqual({a["agent_id"] for a in result["agents"]}, {"a1", "a2"})

    async def test_owner_sees_only_their_own_agent(self):
        await self.add(_agent("a1", "Mine", user_id=OWNER.id), _agent("a2", "Someone Else's", user_id="other-user"))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=OWNER, db=self.db)
        self.assertEqual({a["agent_id"] for a in result["agents"]}, {"a1"})

    async def test_stranger_with_no_agents_sees_nothing(self):
        await self.add(_agent("a1", "Mine", user_id=OWNER.id))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=STRANGER, db=self.db)
        self.assertEqual(result["agents"], [])

    async def test_requesting_someone_elses_agent_id_is_404(self):
        await self.add(_agent("a1", "Mine", user_id=OWNER.id), _agent("a2", "Someone Else's", user_id="other-user"))
        with self.assertRaises(HTTPException) as ctx:
            await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id="a2", user=OWNER, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_filtering_by_agent_id_returns_only_that_agent(self):
        await self.add(_agent("a1", "One", user_id=OWNER.id), _agent("a2", "Two", user_id=OWNER.id))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id="a1", user=OWNER, db=self.db)
        self.assertEqual([a["agent_id"] for a in result["agents"]], ["a1"])

    async def test_admin_requesting_a_nonexistent_agent_id_is_also_404(self):
        """vids ist None fuer Admins (kein Filter) — der Nichtexistenz-Check
        muss trotzdem greifen, sonst kommt fuer Admins still {agents: []}
        zurueck statt eines 404 wie fuer alle anderen."""
        await self.add(_agent("a1", "Mine", user_id=OWNER.id))
        with self.assertRaises(HTTPException) as ctx:
            await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id="does-not-exist", user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)


class MultiAgentShapeTests(ActivityTimelineTestBase):
    async def test_every_visible_agent_appears_even_with_no_activity(self):
        """Ein Agent ohne Aufgaben/Zeitplaene an diesem Tag muss trotzdem als
        leere Zeile erscheinen — sichtbarer Leerlauf, kein Verschwinden."""
        await self.add(_agent("a1", "Busy"), _agent("a2", "Idle"))
        await self.add(_task("a1", DAY_START + timedelta(hours=1), DAY_START + timedelta(hours=2)))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        by_id = {a["agent_id"]: a for a in result["agents"]}
        self.assertEqual(len(by_id["a1"]["tasks"]), 1)
        self.assertEqual(by_id["a2"]["tasks"], [])
        self.assertEqual(by_id["a2"]["scheduled_marks"], [])

    async def test_tasks_are_scoped_to_their_own_agent_not_leaked_across_rows(self):
        await self.add(_agent("a1", "One"), _agent("a2", "Two"))
        await self.add(_task("a1", DAY_START + timedelta(hours=1), DAY_START + timedelta(hours=2)))
        result = await get_activity_timeline(start=DAY_START, end=DAY_END, agent_id=None, user=ADMIN, db=self.db)
        by_id = {a["agent_id"]: a for a in result["agents"]}
        self.assertEqual(len(by_id["a1"]["tasks"]), 1)
        self.assertEqual(by_id["a2"]["tasks"], [])


if __name__ == "__main__":
    unittest.main()
