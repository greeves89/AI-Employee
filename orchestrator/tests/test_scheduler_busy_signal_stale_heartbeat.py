"""#1010: the Redis busy-signal (``queue_depth``/``current_task``) carries no
age limit at all. If the run that set ``current_task`` dies before it ever
reaches the next ``publish_status("idle")`` (crash, OOM-kill, a dead
container), the agent is treated as "busy" forever — every schedule of that
agent is skipped on every tick, with no self-healing path. Measured in
production: 29 hours, ~10 schedules, 30+ dropped slots (issue #1010).

The fix asks the DB instead of Redis: ``current_task`` is only trusted while
the matching ``Task`` row is RUNNING *and* its heartbeat (``updated_at``,
kept fresh every ~60s by ``task_router.handle_task_heartbeat`` while the task
genuinely runs, #692) is within the existing stale-task threshold (#838) —
the same one already used to detect hung tasks on the DOWN/BLOCKED path.
Redis' own ``last_heartbeat`` field is NOT used for this: it is only written
on state transitions (idle<->working), not periodically, so a real
long-running task (a render, a long build) would look "stale" in Redis long
before it's actually stuck — that would have been a regression, not a fix.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus
from app.services.scheduler_service import SchedulerService


class _FakeDb:
    """Resolves ``select(Agent)...`` to the fixed agent (duty-check path) and
    ``select(Task)...`` to whatever ``task`` this case was built with —
    discriminated via SQLAlchemy's own ``column_descriptions``, not by
    guessing at query text."""

    def __init__(self, agent, task=None):
        self.agent = agent
        self.task = task

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"] if stmt.column_descriptions else None
        res = MagicMock()
        res.scalar_one_or_none.return_value = self.task if entity is Task else self.agent
        res.scalars.return_value.all.return_value = []
        return res

    async def commit(self):
        pass


class _FakeRedis:
    def __init__(self, *, queue_depth=0, current_task="", lock_token="tok"):
        self.client = SimpleNamespace(
            incr=AsyncMock(return_value=1), expire=AsyncMock(return_value=True),
            set=AsyncMock(return_value=True), get=AsyncMock(return_value=None),
            delete=AsyncMock(return_value=0), publish=AsyncMock(return_value=1),
        )
        self._queue_depth = queue_depth
        self._status = {"current_task": current_task}
        self._lock_token = lock_token
        self.released = []

    async def get_queue_depth(self, _agent_id):
        return self._queue_depth

    async def get_agent_status(self, _agent_id):
        return dict(self._status)

    async def acquire_dispatch_lock(self, _agent_id, ttl_seconds=20):
        return self._lock_token

    async def release_dispatch_lock(self, agent_id, token):
        self.released.append((agent_id, token))


class _FakeRouter:
    def __init__(self):
        self.create_and_route_task = AsyncMock(return_value=SimpleNamespace(id="task-999"))


class _BaseCase(unittest.IsolatedAsyncioTestCase):
    def _agent(self):
        return Agent(id="agent-1", name="Agent", state=AgentState.RUNNING, config={})

    def _schedule(self, now):
        return Schedule(
            id="plain-1", name="Custom job", prompt="do the thing",
            cron_expression="0 9 * * *", timezone="UTC", interval_seconds=3600,
            agent_id="agent-1", next_run_at=now, enabled=True,
            total_runs=0, success_count=0, fail_count=0,
        )

    def _task(self, *, status=TaskStatus.RUNNING, age=timedelta(0), task_id="task-456"):
        return Task(
            id=task_id, title="t", prompt="p", status=status, agent_id="agent-1",
            updated_at=self.now - age,
        )


class StaleCurrentTaskUnblocksDispatchTests(_BaseCase):
    """The actual bug: current_task alone, with no fresh DB heartbeat behind
    it, must stop being treated as busy — otherwise it blocks forever."""

    def setUp(self):
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        self.agent = self._agent()

    async def _run(self, redis, task):
        svc = SchedulerService(redis=redis)
        svc._stale_task_count = AsyncMock(return_value=0)
        router = _FakeRouter()
        schedule = self._schedule(self.now)
        await svc._execute_schedule(_FakeDb(self.agent, task), router, schedule, self.now)
        return router, schedule

    async def test_task_row_stale_unblocks_dispatch(self):
        # Task is RUNNING in the DB, but its heartbeat stopped 4h ago — well
        # past the default 180-min stale threshold: a crashed run, not a long one.
        redis = _FakeRedis(queue_depth=0, current_task="task-456")
        task = self._task(status=TaskStatus.RUNNING, age=timedelta(hours=4))
        router, schedule = await self._run(redis, task)
        router.create_and_route_task.assert_awaited_once()
        self.assertEqual(schedule.last_run_at, self.now)

    async def test_task_row_missing_unblocks_dispatch(self):
        # Redis names a task the DB has no record of at all — a pure data
        # leftover. There's no way it's "still running".
        redis = _FakeRedis(queue_depth=0, current_task="task-456")
        router, schedule = await self._run(redis, task=None)
        router.create_and_route_task.assert_awaited_once()

    async def test_task_row_already_terminal_unblocks_dispatch(self):
        # The task itself finished (completed/failed) but Redis' current_task
        # was never cleared (the crash happened between completion and the
        # idle transition) — also not "still running".
        redis = _FakeRedis(queue_depth=0, current_task="task-456")
        task = self._task(status=TaskStatus.COMPLETED, age=timedelta(hours=4))
        router, schedule = await self._run(redis, task)
        router.create_and_route_task.assert_awaited_once()

    async def test_long_but_actively_heartbeating_task_still_blocks_dispatch(self):
        # Counterproof against the regression a naive Redis-timestamp fix
        # would introduce: a task running 4h (past where a Redis-only
        # heartbeat would look stale) but whose DB heartbeat is 30s old
        # (task:heartbeat still ticking) is genuinely alive and must still
        # block dispatch — overriding it would double-dispatch the agent.
        redis = _FakeRedis(queue_depth=0, current_task="task-456")
        task = self._task(status=TaskStatus.RUNNING, age=timedelta(seconds=30))
        router, schedule = await self._run(redis, task)
        router.create_and_route_task.assert_not_awaited()

    async def test_task_just_under_schwelle_still_blocks_dispatch(self):
        redis = _FakeRedis(queue_depth=0, current_task="task-456")
        task = self._task(status=TaskStatus.RUNNING, age=timedelta(minutes=179))
        router, schedule = await self._run(redis, task)
        router.create_and_route_task.assert_not_awaited()

    async def test_task_just_over_schwelle_unblocks_dispatch(self):
        redis = _FakeRedis(queue_depth=0, current_task="task-456")
        task = self._task(status=TaskStatus.RUNNING, age=timedelta(minutes=181))
        router, schedule = await self._run(redis, task)
        router.create_and_route_task.assert_awaited_once()

    async def test_queue_depth_still_blocks_despite_stale_task(self):
        # A non-empty queue is a separate, real signal (#1010's own "Analog
        # für eine Queue-Zeile" follow-up is explicitly out of scope here) —
        # staleness of current_task must never override it.
        redis = _FakeRedis(queue_depth=1, current_task="task-456")
        task = self._task(status=TaskStatus.RUNNING, age=timedelta(hours=4))
        router, schedule = await self._run(redis, task)
        router.create_and_route_task.assert_not_awaited()

    async def test_meeting_marker_never_overridden(self):
        # "room:<id>" is a meeting marker, not a Task id — there's no Task
        # row to check staleness against, and it must stay busy as before
        # rather than being treated as an orphaned reference (that would
        # cut a live meeting's busy-protection, not fix #1010's class at all).
        redis = _FakeRedis(queue_depth=0, current_task="room:abc")
        router, schedule = await self._run(redis, task=None)
        router.create_and_route_task.assert_not_awaited()

    async def test_message_marker_never_overridden(self):
        redis = _FakeRedis(queue_depth=0, current_task="msg:abc")
        router, schedule = await self._run(redis, task=None)
        router.create_and_route_task.assert_not_awaited()


class CurrentTaskAbgestandenUnitTests(unittest.IsolatedAsyncioTestCase):
    """Direct unit coverage of the gating helper itself."""

    def setUp(self):
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        self.svc = SchedulerService(redis=_FakeRedis())

    async def test_db_lookup_failure_fails_closed(self):
        class _BrokenDb:
            async def execute(self, _stmt):
                raise RuntimeError("db down")

        result = await self.svc._current_task_abgestanden(_BrokenDb(), "agent-1", "task-1", self.now)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
