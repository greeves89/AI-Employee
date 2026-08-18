"""A schedule skipped because its agent is OFF_DUTY (alive, but outside its
configured working hours) used to lose the whole day — next_run_at jumped
straight to _calc_next_run(now), same anti-pattern the overload fix already
solved for a different skip reason (#605/v1.220.4). This generalizes that fix
to the OFF_DUTY branch, reusing the same _retry_or_advance helper.

Note: a genuinely DOWN/BLOCKED agent takes a different code path entirely
(needs_handover → escalate_failure) that never advances next_run_at at all —
it retries every tick with no cap, so it does not lose a day either way. The
branch this test covers is the "else" — anything not handover-worthy and not
overloaded, which today is only OFF_DUTY.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService, _calc_next_run


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
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.deleted: list[str] = []

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        self.expirations[key] = seconds
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return 1


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()

    async def get_queue_depth(self, _agent_id):
        return 0


class SchedulerOffDutyRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 03:00 UTC — outside the 09:00-17:00 working hours configured below.
        self.now = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
        self.agent = Agent(
            id="agent-off-duty",
            name="Agent",
            state=AgentState.RUNNING,
            config={"working_hours": {"start": "09:00", "end": "17:00", "timezone": "UTC"}},
        )
        self.redis = _FakeRedis()
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self):
        return Schedule(
            id="rhythmus-morning",
            name="[Rhythmus] Morgencheck",
            prompt="placeholder",
            cron_expression="0 7 * * *",
            timezone="UTC",
            interval_seconds=24 * 3600,
            agent_id=self.agent.id,
            next_run_at=self.now,
            enabled=True,
        )

    async def _execute_once(self, schedule):
        await self.svc._execute_schedule(
            _FakeDb(self.agent), SimpleNamespace(), schedule, self.now,
        )

    async def test_first_and_second_off_duty_tick_retry_shortly(self):
        schedule = self._schedule()

        await self._execute_once(schedule)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(minutes=12))

        await self._execute_once(schedule)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(minutes=12))

    async def test_third_consecutive_off_duty_tick_gives_up_for_today(self):
        schedule = self._schedule()
        regular_next = _calc_next_run(schedule, self.now)

        await self._execute_once(schedule)
        await self._execute_once(schedule)
        await self._execute_once(schedule)

        self.assertEqual(schedule.next_run_at, regular_next)
        self.assertIn("schedule:retry:off_duty:rhythmus-morning", self.redis.client.deleted)

    async def test_off_duty_and_overload_use_separate_retry_budgets(self):
        """Different skip reasons on the same schedule id must not share a
        retry counter — each gets its own Redis key."""
        schedule = self._schedule()
        await self._execute_once(schedule)
        self.assertIn("schedule:retry:off_duty:rhythmus-morning", self.redis.client.values)
        self.assertNotIn("schedule:retry:overload:rhythmus-morning", self.redis.client.values)


if __name__ == "__main__":
    unittest.main()
