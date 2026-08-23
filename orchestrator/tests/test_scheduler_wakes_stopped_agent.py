"""Ein gestoppter Agent muss fuer einen faelligen Zeitplan geweckt werden (#632).

Agenten werden vom UserLifecycle nach 30 Minuten Nutzer-Inaktivitaet gestoppt.
Fiel danach ein Zeitplan oder ein Kalender-Block an, galt der Agent als DOWN:
kein Task, keine Verschiebung von ``next_run_at`` — der Lauf verschwand spurlos,
und jeder 30-Sekunden-Tick meldete denselben Ausfall neu. Podcast-Slots an
~1/3 der Tage weg, ohne Spur.

Jetzt: erst wecken (``ensure_agent_running``), dann normal weiter. Scheitert
das Wecken, wird eskaliert UND der Lauf kurz nachgesetzt statt ewig zu haengen.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService


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

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        return True

    async def delete(self, key):
        self.values.pop(key, None)
        return 1

    async def set(self, *args, **kwargs):
        return True


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()

    async def get_queue_depth(self, _agent_id):
        return 0


class _Reached(Exception):
    """Sentinel: der Code hat den 'arbeitsfaehig'-Pfad erreicht."""


class SchedulerWakesStoppedAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 23, 5, 0, tzinfo=timezone.utc)
        self.agent = Agent(id="agent-asleep", name="Podcast-Agent", state=AgentState.STOPPED, config={})
        self.redis = _FakeRedis()
        self.svc = SchedulerService(redis=self.redis, docker_service=SimpleNamespace())
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self):
        return Schedule(
            id="podcast-0700",
            name="Taeglicher KI-News-Podcast (07:00)",
            prompt="placeholder",
            cron_expression="0 7 * * *",
            timezone="Europe/Berlin",
            interval_seconds=24 * 3600,
            agent_id=self.agent.id,
            next_run_at=self.now,
            enabled=True,
        )

    async def _execute_once(self, schedule):
        await self.svc._execute_schedule(_FakeDb(self.agent), SimpleNamespace(), schedule, self.now)

    async def test_stopped_agent_is_woken_and_run_proceeds(self):
        wake = AsyncMock(return_value=True)

        async def reached(*_args, **_kwargs):
            raise _Reached()

        with patch("app.core.agent_wakeup.ensure_agent_running", wake), \
             patch("app.services.duty_service.escalate_silence", reached), \
             patch("app.services.duty_service.escalate_failure", AsyncMock()) as failure:
            with self.assertRaises(_Reached):
                await self._execute_once(self._schedule())

        wake.assert_awaited_once()
        self.assertEqual(wake.await_args.args[0], self.agent.id)
        failure.assert_not_awaited()

    async def test_wake_failure_escalates_and_reschedules_instead_of_hanging(self):
        schedule = self._schedule()
        with patch("app.core.agent_wakeup.ensure_agent_running", AsyncMock(return_value=False)), \
             patch("app.services.duty_service.escalate_failure", AsyncMock()) as failure:
            await self._execute_once(schedule)

        failure.assert_awaited_once()
        # Vorher blieb next_run_at in der Vergangenheit stehen (Dauerschleife);
        # jetzt wird wie bei off_duty kurz nachgesetzt.
        self.assertGreater(schedule.next_run_at, self.now)
        self.assertLessEqual(schedule.next_run_at, self.now + timedelta(hours=1))

    async def test_running_agent_is_not_woken(self):
        self.agent.state = AgentState.RUNNING
        wake = AsyncMock(return_value=True)

        async def reached(*_args, **_kwargs):
            raise _Reached()

        with patch("app.core.agent_wakeup.ensure_agent_running", wake), \
             patch("app.services.duty_service.escalate_silence", reached):
            with self.assertRaises(_Reached):
                await self._execute_once(self._schedule())
        wake.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
