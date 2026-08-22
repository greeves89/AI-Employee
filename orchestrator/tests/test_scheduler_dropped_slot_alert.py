"""Giving up on a due slot used to be the one scheduler state change that was
neither counted nor reported (#631): the skip branch returns before
``total_runs += 1``, and ``_retry_or_advance`` pushes ``next_run_at`` into the
future — so the failure watchdog saw ``drift == 0``, the missed-schedule
watchdog found nothing (it looks for ``next_run_at`` in the past), and
``success_rate`` stayed at 1.0. A daily schedule could miss days in a row and
still report a perfect record.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService
from app.services.watchdog import md_escape


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDb:
    def __init__(self, agent):
        self.agent = agent

    async def execute(self, _stmt):
        return _ScalarResult(self.agent)


class _FakeRedisClient:
    def __init__(self):
        self.values: dict[str, object] = {}
        self.published: list[tuple[str, str]] = []

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        return True

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()

    async def get_queue_depth(self, _agent_id):
        return 0


class DroppedSlotAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 03:00 UTC — outside the 09:00-17:00 working hours, so every tick
        # takes the OFF_DUTY skip branch.
        self.now = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)
        self.agent = Agent(
            id="agent-off-duty",
            name="Agent",
            state=AgentState.RUNNING,
            config={"working_hours": {"start": "09:00", "end": "17:00", "timezone": "UTC"}},
        )
        self.redis = _FakeRedis()
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)
        self.schedule = Schedule(
            id="daily-podcast",
            name="Daily job",
            prompt="placeholder",
            cron_expression="0 7 * * *",
            timezone="UTC",
            interval_seconds=24 * 3600,
            agent_id=self.agent.id,
            next_run_at=self.now,
            enabled=True,
            total_runs=0,
            success_count=0,
            fail_count=0,
        )

    async def _execute_once(self):
        await self.svc._execute_schedule(
            _FakeDb(self.agent), SimpleNamespace(), self.schedule, self.now,
        )

    async def test_retries_within_budget_stay_silent(self):
        await self._execute_once()
        await self._execute_once()

        self.assertEqual(self.schedule.total_runs, 0)
        self.assertEqual(self.schedule.fail_count, 0)
        self.assertEqual(self.redis.client.published, [])

    async def test_dropped_slot_is_counted_and_reported(self):
        await self._execute_once()
        await self._execute_once()
        await self._execute_once()  # budget exhausted — today's slot is gone

        # Counted as a run that failed, so success_rate stops reporting 1.0.
        self.assertEqual(self.schedule.total_runs, 1)
        self.assertEqual(self.schedule.fail_count, 1)
        # drift stays 0, so the failure watchdog is not tripped by our own entry.
        self.assertEqual(
            self.schedule.total_runs
            - (self.schedule.success_count + self.schedule.fail_count),
            0,
        )
        # No run happened, so last_run_at must not pretend otherwise.
        self.assertIsNone(self.schedule.last_run_at)

        self.assertEqual(len(self.redis.client.published), 1)
        channel, raw = self.redis.client.published[0]
        self.assertEqual(channel, "telegram:notification")
        text = json.loads(raw)["text"]
        self.assertIn("Daily job", text)
        self.assertIn(md_escape("off_duty"), text)

    async def test_report_names_the_original_slot_not_the_last_retry(self):
        for _ in range(3):
            await self._execute_once()

        text = json.loads(self.redis.client.published[0][1])["text"]
        self.assertIn(self.now.isoformat(), text)
        self.assertNotIn((self.now + timedelta(minutes=12)).isoformat(), text)

    async def test_repeated_drops_are_throttled_to_one_alert(self):
        """Ein Zeitplan, der oefter als stuendlich laeuft, verwirft bei einer langen
        Stoerung alle ~25 Minuten einen Slot — gemeldet wird das einmal."""
        for _ in range(6):
            await self._execute_once()

        self.assertEqual(self.schedule.fail_count, 2)
        self.assertEqual(len(self.redis.client.published), 1)

    async def test_successful_run_resets_the_retry_budget(self):
        """Die Zaehler leben 6 Stunden. Ohne Ruecksetzen erbt der naechste Slot eines
        stuendlichen Zeitplans das aufgebrauchte Budget des vorherigen — und die
        Meldung naennte den alten, laengst gelaufenen Soll-Slot."""
        await self._execute_once()
        self.assertIn("schedule:retry:off_duty:daily-podcast", self.redis.client.values)

        await self.svc._clear_retry_budgets(self.schedule)

        self.assertEqual(self.redis.client.values, {})

    async def test_one_shot_schedule_loses_nothing_and_stays_silent(self):
        """Ein Einmal-Lauf (Plan-Block) behaelt seinen Auftrag und versucht es in 60
        Sekunden wieder — er verliert keinen Termin und darf keinen melden."""
        self.schedule.cron_expression = None
        self.schedule.interval_seconds = 0

        for _ in range(3):
            await self._execute_once()

        self.assertEqual(self.schedule.total_runs, 0)
        self.assertEqual(self.schedule.fail_count, 0)
        self.assertEqual(self.redis.client.published, [])


if __name__ == "__main__":
    unittest.main()
