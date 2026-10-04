"""Ein gestoppter Agent muss fuer einen faelligen Zeitplan geweckt werden (#632).

Agenten werden vom UserLifecycle nach 30 Minuten Nutzer-Inaktivitaet gestoppt.
Fiel danach ein Zeitplan oder ein Kalender-Block an, galt der Agent als DOWN:
kein Task, keine Verschiebung von ``next_run_at`` — der Lauf verschwand spurlos,
und jeder 30-Sekunden-Tick meldete denselben Ausfall neu. Podcast-Slots an
~1/3 der Tage weg, ohne Spur.

Jetzt: erst wecken (``ensure_agent_running``), dann normal weiter. Scheitert
das Wecken, wird eskaliert UND der Lauf kurz nachgesetzt statt ewig zu haengen.

#912: Geweckt wird aber nur, wenn der Lauf danach auch stattfindet. Vorher
weckte der Scheduler einen gestoppten Agenten fuer den [Proactive]-Lauf und
uebersprang ihn erst danach („keine Verantwortungsbereiche") — der Container
blieb an. Und bewusst vom Nutzer angehaltene Agenten wurden fuer jeden
faelligen Zeitplan wieder hochgefahren. Jetzt wird VOR dem Wecken geprueft:
ohne Bereiche, ausserhalb der Dienstzeit oder vom Nutzer angehalten heisst
nicht wecken und den Zeitplan weiterruecken. Idle-gestoppte Agenten mit
Bereichen werden weiter geweckt (#632 bleibt geloest).
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        self.added: list = []

    async def execute(self, _stmt):
        return _ScalarResult(self.agent)

    def add(self, obj):
        self.added.append(obj)


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


class _Basis(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 23, 5, 0, tzinfo=timezone.utc)
        self.agent = Agent(id="agent-asleep", name="Podcast-Agent", state=AgentState.STOPPED, config={})
        self.redis = _FakeRedis()
        self.svc = SchedulerService(redis=self.redis, docker_service=SimpleNamespace())
        self.svc._stale_task_count = AsyncMock(return_value=0)

    def _schedule(self, name="Taeglicher KI-News-Podcast (07:00)"):
        return Schedule(
            id="podcast-0700",
            name=name,
            prompt="placeholder",
            cron_expression="0 7 * * *",
            timezone="Europe/Berlin",
            interval_seconds=24 * 3600,
            agent_id=self.agent.id,
            next_run_at=self.now,
            enabled=True,
        )

    async def _execute_once(self, schedule):
        self.db = _FakeDb(self.agent)
        await self.svc._execute_schedule(self.db, SimpleNamespace(), schedule, self.now)


class SchedulerWakesStoppedAgentTests(_Basis):
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
             patch("app.services.duty_service.escalate_failure", AsyncMock()) as failure, \
             patch("app.services.duty_service.escalate_skipped_run", AsyncMock()) as trace:
            await self._execute_once(schedule)

        failure.assert_awaited_once()
        trace.assert_awaited_once()  # die Spur aus PR #643 entsteht genau hier
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


class NichtWeckenWennDerLaufNichtStattfindet(_Basis):
    """#912 — die Pruefungen, die einen Lauf ohnehin ueberspringen, kommen VOR dem Wecken."""

    async def _ohne_wecken(self, schedule):
        wake = AsyncMock(return_value=True)
        with patch("app.core.agent_wakeup.ensure_agent_running", wake), \
             patch("app.services.duty_service.escalate_failure", AsyncMock()) as failure, \
             patch("app.services.duty_service.escalate_skipped_run", AsyncMock()) as trace:
            await self._execute_once(schedule)
        wake.assert_not_awaited()
        failure.assert_not_awaited()   # kein DOWN — er ist nicht ausgefallen
        trace.assert_not_awaited()
        self.assertGreater(schedule.next_run_at, self.now, "Zeitplan rueckt weiter statt zu haengen.")

    async def test_proaktiv_ohne_bereiche_wird_nicht_geweckt(self):
        await self._ohne_wecken(self._schedule(name="[Proactive] Podcast-Agent"))

    async def test_rhythmus_ohne_bereiche_wird_nicht_geweckt(self):
        await self._ohne_wecken(self._schedule(name="[Rhythmus] Morgencheck"))

    async def test_ausserhalb_der_dienstzeit_wird_nicht_geweckt(self):
        # 05:00 UTC, Dienst 08:00–17:00 UTC
        self.agent.config = {"working_hours": {"start": "08:00", "end": "17:00", "timezone": "UTC"}}
        await self._ohne_wecken(self._schedule())

    async def test_vom_nutzer_angehalten_wird_nicht_geweckt(self):
        self.agent.config = {"angehalten_vom_nutzer": True,
                             "proactive": {"responsibilities": [{"title": "Podcast"}]}}
        schedule = self._schedule(name="[Proactive] Podcast-Agent")
        await self._ohne_wecken(schedule)
        self.assertFalse(self.db.added, "Ein angehaltener Agent bekommt keinen Auftrags-Hinweis.")

    async def test_idle_gestoppt_mit_bereichen_wird_geweckt(self):
        """Regressionsschutz #632: gestoppt OHNE Nutzer-Halt, MIT Bereichen, im Dienst."""
        self.agent.config = {"proactive": {"enabled": True, "responsibilities": [{"title": "Podcast"}]}}
        wake = AsyncMock(return_value=True)

        async def reached(*_args, **_kwargs):
            raise _Reached()

        with patch("app.core.agent_wakeup.ensure_agent_running", wake), \
             patch("app.services.duty_service.escalate_silence", reached):
            with self.assertRaises(_Reached):
                await self._execute_once(self._schedule(name="[Proactive] Podcast-Agent"))
        wake.assert_awaited_once()


class NutzerHaltAmStoppUndStart(unittest.IsolatedAsyncioTestCase):
    """Nur der Stopp durch den Nutzer setzt den Halt; sein Start hebt ihn auf."""

    def setUp(self):
        self.agent = Agent(id="a1", name="A", state=AgentState.RUNNING, config={"role": "x"})
        self.manager = SimpleNamespace(
            _get_agent=AsyncMock(return_value=self.agent),
            stop_agent=AsyncMock(return_value=self.agent),
            start_agent=AsyncMock(return_value=self.agent),
            restart_agent=AsyncMock(return_value=self.agent),
            get_agent_with_metrics=AsyncMock(return_value={}),
        )
        self.db = SimpleNamespace(commit=AsyncMock())

    async def test_stopp_setzt_start_hebt_auf(self):
        from app.api import agents as agents_api
        from app.core import agent_duty

        with patch.object(agents_api, "_check_owner", AsyncMock()):
            await agents_api.stop_agent("a1", user=SimpleNamespace(id="u1"), db=self.db, manager=self.manager)
            self.assertTrue(agent_duty.vom_nutzer_angehalten(self.agent))
            await agents_api.start_agent("a1", user=SimpleNamespace(id="u1"), db=self.db, manager=self.manager)
        self.assertFalse(agent_duty.vom_nutzer_angehalten(self.agent))
        self.assertEqual(self.agent.config.get("role"), "x", "Der Rest der Konfiguration bleibt.")

    async def test_neustart_hebt_auf(self):
        from app.api import agents as agents_api
        from app.core import agent_duty

        self.agent.config = {"angehalten_vom_nutzer": True}
        with patch.object(agents_api, "_check_owner", AsyncMock()), \
             patch.object(agents_api, "AgentResponse", MagicMock()):
            await agents_api.restart_agent("a1", user=SimpleNamespace(id="u1"), db=self.db, manager=self.manager)
        self.assertFalse(agent_duty.vom_nutzer_angehalten(self.agent))


if __name__ == "__main__":
    unittest.main()
