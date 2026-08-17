import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
        return 5


class SchedulerOverloadRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 17, 6, 0, tzinfo=timezone.utc)
        self.agent = Agent(
            id="agent-overloaded",
            name="Agent",
            state=AgentState.RUNNING,
            config={},
        )
        self.redis = _FakeRedis()
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self):
        return Schedule(
            id="daily-overload",
            name="Daily job",
            prompt="Run",
            cron_expression="0 6 * * *",
            timezone="UTC",
            interval_seconds=24 * 3600,
            agent_id=self.agent.id,
            next_run_at=self.now,
            enabled=True,
        )

    async def _execute_once(self, schedule):
        with patch("app.services.duty_service.escalate_overload", new=AsyncMock()):
            await self.svc._execute_schedule(
                _FakeDb(self.agent),
                SimpleNamespace(),
                schedule,
                self.now,
            )

    async def test_first_and_second_overload_tick_retry_shortly(self):
        schedule = self._schedule()

        await self._execute_once(schedule)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(minutes=12))
        self.assertEqual(
            self.redis.client.expirations["schedule:overload-retry:daily-overload"],
            6 * 3600,
        )

        await self._execute_once(schedule)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(minutes=12))

    async def test_third_consecutive_overload_tick_returns_to_regular_slot(self):
        schedule = self._schedule()
        regular_next = _calc_next_run(schedule, self.now)

        await self._execute_once(schedule)
        await self._execute_once(schedule)
        await self._execute_once(schedule)

        self.assertEqual(schedule.next_run_at, regular_next)
        self.assertIn(
            "schedule:overload-retry:daily-overload",
            self.redis.client.deleted,
        )


if __name__ == "__main__":
    unittest.main()
