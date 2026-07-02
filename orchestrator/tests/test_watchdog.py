"""Watchdog tests for issue #211: stale-task + missed-schedule detection.

Covers the two acceptance-criteria tests:
  - test_watchdog_detects_stale_task
  - test_watchdog_detects_missed_schedule

The detection functions apply a defensive Python-side predicate on top of the
SQL WHERE clause, so these tests exercise the real staleness/miss logic with an
AsyncMock db (no Postgres needed).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus
from app.services.watchdog import (
    find_missed_schedules,
    find_stale_tasks,
    is_schedule_missed,
    is_task_stale,
    mark_task_stale,
)


def _mock_db(rows):
    db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=result)
    return db


def _task(task_id, status, updated_minutes_ago, now):
    t = Task()
    t.id = task_id
    t.title = f"Task {task_id}"
    t.status = status
    t.updated_at = now - timedelta(minutes=updated_minutes_ago)
    t.metadata_ = {}
    return t


def _schedule(sid, enabled, next_run_offset_min, now):
    s = Schedule()
    s.id = sid
    s.name = f"Schedule {sid}"
    s.enabled = enabled
    s.next_run_at = now + timedelta(minutes=next_run_offset_min)
    return s


class StaleTaskWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_watchdog_detects_stale_task(self):
        now = datetime.now(timezone.utc)
        stale = _task("t1", TaskStatus.RUNNING, updated_minutes_ago=40, now=now)
        fresh = _task("t2", TaskStatus.RUNNING, updated_minutes_ago=5, now=now)
        done = _task("t3", TaskStatus.COMPLETED, updated_minutes_ago=99, now=now)

        db = _mock_db([stale, fresh, done])
        found = await find_stale_tasks(db, now)

        ids = {t.id for t in found}
        self.assertEqual(ids, {"t1"}, "only the RUNNING task with no >30min heartbeat is stale")

    async def test_mark_task_stale_flips_to_failed(self):
        now = datetime.now(timezone.utc)
        task = _task("t1", TaskStatus.RUNNING, updated_minutes_ago=45, now=now)

        mark_task_stale(task, now)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertTrue(task.metadata_["stale"])
        self.assertEqual(task.completed_at, now)
        self.assertIn("heartbeat", (task.error or "").lower())

    def test_is_task_stale_predicate(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(is_task_stale(_task("a", TaskStatus.RUNNING, 31, now), now))
        self.assertFalse(is_task_stale(_task("b", TaskStatus.RUNNING, 29, now), now))
        self.assertFalse(is_task_stale(_task("c", TaskStatus.PENDING, 99, now), now))

    def test_is_task_stale_handles_naive_datetime(self):
        now = datetime.now(timezone.utc)
        t = _task("a", TaskStatus.RUNNING, 40, now)
        t.updated_at = t.updated_at.replace(tzinfo=None)  # simulate naive DB value
        self.assertTrue(is_task_stale(t, now))


class MissedScheduleWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_watchdog_detects_missed_schedule(self):
        now = datetime.now(timezone.utc)
        missed = _schedule("s1", enabled=True, next_run_offset_min=-10, now=now)
        upcoming = _schedule("s2", enabled=True, next_run_offset_min=+30, now=now)
        disabled = _schedule("s3", enabled=False, next_run_offset_min=-60, now=now)

        db = _mock_db([missed, upcoming, disabled])
        found = await find_missed_schedules(db, now)

        ids = {s.id for s in found}
        self.assertEqual(ids, {"s1"}, "only the enabled, overdue schedule is missed")

    def test_is_schedule_missed_predicate(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(is_schedule_missed(_schedule("a", True, -6, now), now))
        self.assertFalse(is_schedule_missed(_schedule("b", True, -4, now), now))
        self.assertFalse(is_schedule_missed(_schedule("c", False, -60, now), now))


if __name__ == "__main__":
    unittest.main()
