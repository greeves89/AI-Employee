"""Ein Zeitplan startet nicht neben seinem eigenen, noch offenen Lauf — und ein
wiederkehrender Lauf erfährt, dass er nicht im Hintergrund warten soll.

Anlass im Betrieb: Ein 30-Minuten-Zeitplan startete, während sein voriger Lauf
noch arbeitete. Die Busy-Prüfung liest den Redis-Status des Agenten; während
eines Chats steht dort der Chat, und eine wartende Wiederholung („Versuch 2")
ist noch nicht in der Warteschlange. Der zweite Lauf las den ganzen Auftrag,
fand die Arbeitssperre und hörte auf — je etwa 2 $, rund zehnmal an einem Tag.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.core.adaptiver_takt import WARTE_HINWEIS
from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService, _calc_next_run

JETZT = datetime(2026, 10, 17, 9, 10, tzinfo=timezone.utc)


class _FakeDb:
    """Antwortet auf die Aufgaben-Abfrage mit den offenen Metadaten, sonst mit dem Agenten."""

    def __init__(self, agent, offene_metadaten):
        self.agent = agent
        self.offen = offene_metadaten

    async def execute(self, stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = self.agent
        ist_aufgaben_abfrage = "tasks.metadata" in str(stmt)
        res.scalars.return_value.all.return_value = list(self.offen) if ist_aufgaben_abfrage else []
        return res

    async def commit(self):
        pass


class _FakeRedis:
    """Agent wirkt frei — genau die Lage, in der die Redis-Prüfung nichts merkt."""

    def __init__(self):
        self.client = MagicMock()

    async def get_queue_depth(self, _agent_id):
        return 0

    async def get_agent_status(self, _agent_id):
        return {"current_task": "chat:sitzung"}

    async def acquire_dispatch_lock(self, _agent_id, ttl_seconds=20):
        return "tok"

    async def release_dispatch_lock(self, agent_id, token):
        return None


class EigenerLaufNochOffen(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = Agent(id="a1", name="Bearbeiter", state=AgentState.RUNNING, config={})
        self.svc = SchedulerService(redis=_FakeRedis())
        self.svc._stale_task_count = AsyncMock(return_value=0)
        self.router = SimpleNamespace(
            create_and_route_task=AsyncMock(return_value=SimpleNamespace(id="t-neu")))

    def _zeitplan(self, **felder):
        werte = dict(id="s1", name="PR-Bearbeiter", prompt="Offene PRs abarbeiten",
                     cron_expression="10,40 * * * *", timezone="UTC", interval_seconds=0,
                     agent_id="a1", next_run_at=JETZT, enabled=True,
                     total_runs=0, success_count=0, fail_count=0)
        werte.update(felder)
        return Schedule(**werte)

    async def _feuern(self, schedule, offen):
        await self.svc._execute_schedule(_FakeDb(self.agent, offen), self.router, schedule, JETZT)

    async def test_offener_eigener_lauf_verhindert_den_start(self):
        zeitplan = self._zeitplan()
        await self._feuern(zeitplan, [{"schedule_id": "s1", "heal_attempt": 1}])
        self.router.create_and_route_task.assert_not_awaited()
        self.assertEqual(zeitplan.next_run_at, _calc_next_run(zeitplan, JETZT))

    async def test_offener_lauf_eines_anderen_zeitplans_stoert_nicht(self):
        await self._feuern(self._zeitplan(), [{"schedule_id": "anderer"}, {}, None])
        self.router.create_and_route_task.assert_awaited_once()

    async def test_einmal_lauf_wird_nicht_gesperrt(self):
        """Ein Tagesplan-Block hat keinen nächsten Termin — Überspringen hieße verlieren."""
        zeitplan = self._zeitplan(cron_expression=None, interval_seconds=0, name="Einmal-Block")
        await self._feuern(zeitplan, [{"schedule_id": "s1"}])
        self.router.create_and_route_task.assert_awaited_once()

    async def test_wiederkehrender_lauf_traegt_den_warte_hinweis(self):
        await self._feuern(self._zeitplan(), [])
        prompt = self.router.create_and_route_task.await_args.kwargs["prompt"]
        self.assertIn(WARTE_HINWEIS.strip(), prompt)

    async def test_einmal_lauf_ohne_warte_hinweis(self):
        await self._feuern(self._zeitplan(cron_expression=None, interval_seconds=0, name="Einmal-Block"), [])
        prompt = self.router.create_and_route_task.await_args.kwargs["prompt"]
        self.assertNotIn("Nicht im Hintergrund warten", prompt)


if __name__ == "__main__":
    unittest.main()
