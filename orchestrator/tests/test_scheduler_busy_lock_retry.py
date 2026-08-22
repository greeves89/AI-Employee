"""A schedule skipped because the agent is momentarily busy, or a dispatch
lock is held for a few seconds by a concurrent tick, used to lose the whole
day the same way an OVERLOADED skip did before #605/v1.220.4 — next_run_at
jumped straight to _calc_next_run(now) with no retry at all. Generalizes the
existing overload-retry fix to these two skip reasons as well.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService, _calc_next_run


class _FakeDb:
    """A live (non-off-duty, non-overloaded) agent also passes through
    duty_service.escalate_silence, which runs its own Notification query — so
    every execute() call here must answer BOTH the scalar_one_or_none() shape
    (agent lookup) and the scalars().all() shape (notification lookup)."""

    def __init__(self, agent):
        self.agent = agent

    async def execute(self, _stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = self.agent
        res.scalars.return_value.all.return_value = []
        return res

    async def commit(self):
        pass


class _FakeRedisClient:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.deleted: list[str] = []
        self.published: list[tuple[str, str]] = []

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
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
            self.deleted.append(key)
            self.values.pop(key, None)
        return len(keys)

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


class _FakeRedis:
    """queue_depth/current_task/lock behavior are all configurable per test —
    that's the whole point: each test drives the agent into exactly one skip
    branch."""

    def __init__(self, *, queue_depth=0, current_task="", lock_token="tok"):
        self.client = _FakeRedisClient()
        self._queue_depth = queue_depth
        self._current_task = current_task
        self._lock_token = lock_token
        self.released = []

    async def get_queue_depth(self, _agent_id):
        return self._queue_depth

    async def get_agent_status(self, _agent_id):
        return {"current_task": self._current_task}

    async def acquire_dispatch_lock(self, _agent_id, ttl_seconds=20):
        return self._lock_token

    async def release_dispatch_lock(self, agent_id, token):
        self.released.append((agent_id, token))


class _BaseCase(unittest.IsolatedAsyncioTestCase):
    def _agent(self, **overrides):
        return Agent(id="agent-1", name="Agent", state=AgentState.RUNNING, config={}, **overrides)

    async def _execute_once(self, svc, agent, schedule, now):
        await svc._execute_schedule(_FakeDb(agent), SimpleNamespace(), schedule, now)


class ProactiveBusyRetryTests(_BaseCase):
    """The [Proactive]-only early busy-check (before prompt building)."""

    def setUp(self):
        self.now = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
        self.agent = self._agent()
        # Busy via an active (non-chat) current_task, not queue depth — avoids
        # tripping the earlier OVERLOADED duty check.
        self.redis = _FakeRedis(queue_depth=0, current_task="task-123")
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self):
        return Schedule(
            id="proactive-1", name="[Proactive] Agent", prompt="placeholder",
            cron_expression="0 9 * * *", timezone="UTC", interval_seconds=3600,
            agent_id=self.agent.id, next_run_at=self.now, enabled=True,
            total_runs=0, success_count=0, fail_count=0,
        )

    async def test_busy_retries_shortly_then_gives_up(self):
        schedule = self._schedule()
        regular_next = _calc_next_run(schedule, self.now)

        await self._execute_once(self.svc, self.agent, schedule, self.now)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(minutes=12))
        await self._execute_once(self.svc, self.agent, schedule, self.now)
        self.assertEqual(schedule.next_run_at, self.now + timedelta(minutes=12))
        await self._execute_once(self.svc, self.agent, schedule, self.now)
        self.assertEqual(schedule.next_run_at, regular_next)
        self.assertIn("schedule:retry:busy:proactive-1", self.redis.client.deleted)


class DispatchLockRetryTests(_BaseCase):
    """The final atomic check, shared by every schedule type — lock held."""

    def setUp(self):
        self.now = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
        self.agent = self._agent()
        self.redis = _FakeRedis(queue_depth=0, current_task="", lock_token=None)
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self):
        # Neither [Proactive] nor [Rhythmus] — a plain scheduled prompt reaches
        # the final atomic check directly without any prompt-building setup.
        return Schedule(
            id="plain-1", name="Custom job", prompt="do the thing",
            cron_expression="0 9 * * *", timezone="UTC", interval_seconds=3600,
            agent_id=self.agent.id, next_run_at=self.now, enabled=True,
            total_runs=0, success_count=0, fail_count=0,
        )

    async def test_lock_held_retries_on_next_tick_then_gives_up(self):
        schedule = self._schedule()
        regular_next = _calc_next_run(schedule, self.now)
        short_delay = self.now + timedelta(seconds=30)

        # max_attempts=3: the first three collisions each get the short retry
        # delay, never jumping straight to tomorrow.
        for _ in range(3):
            await self._execute_once(self.svc, self.agent, schedule, self.now)
            self.assertEqual(schedule.next_run_at, short_delay)

        # 4th collision: budget exhausted, gives up on today's slot.
        await self._execute_once(self.svc, self.agent, schedule, self.now)
        self.assertEqual(schedule.next_run_at, regular_next)
        self.assertIn("schedule:retry:lock:plain-1", self.redis.client.deleted)


class FinalBusyCheckRetryTests(_BaseCase):
    """The final atomic check, shared by every schedule type — agent busy."""

    def setUp(self):
        self.now = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
        self.agent = self._agent()
        self.redis = _FakeRedis(queue_depth=0, current_task="task-456", lock_token="tok")
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self):
        return Schedule(
            id="plain-2", name="Custom job", prompt="do the thing",
            cron_expression="0 9 * * *", timezone="UTC", interval_seconds=3600,
            agent_id=self.agent.id, next_run_at=self.now, enabled=True,
            total_runs=0, success_count=0, fail_count=0,
        )

    async def test_busy_at_final_check_retries_then_gives_up(self):
        schedule = self._schedule()
        regular_next = _calc_next_run(schedule, self.now)

        for _ in range(3):
            await self._execute_once(self.svc, self.agent, schedule, self.now)
        self.assertEqual(schedule.next_run_at, regular_next)
        # The held lock must be released back on every busy-skip, not leaked.
        self.assertEqual(len(self.redis.released), 3)


if __name__ == "__main__":
    unittest.main()
