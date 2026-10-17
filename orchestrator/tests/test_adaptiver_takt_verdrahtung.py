"""Ist der adaptive Takt überhaupt VERDRAHTET? (#941, Punkte 3 und 4)

Die unangenehmste Fehlerklasse dieses Features ist nicht ein falsches Ergebnis,
sondern gar keines: Rechenregel und Werkzeug sind korrekt, alle Tests grün — und
der Takt bewegt sich trotzdem nie, weil niemand dem Lauf gesagt hat, dass er
melden soll. Deshalb prüfen diese Tests nicht die Rechnung, sondern die beiden
Stellen, an denen sie an die laufende Anlage angeschlossen ist:

  * Der Prompt eines adaptiven Zeitplan-Laufs trägt den Melde-Auftrag, und die
    Auftrags-Metadaten tragen die Eigenschaft mit.
  * Der Eigeninitiative-Abgleich und der adaptive Takt überschreiben sich nicht
    gegenseitig.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.core.adaptiver_takt import MELDE_HINWEIS, WARTE_HINWEIS
from app.core.eigeninitiative import _takt_abweicht, abgleichen
from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.services.scheduler_service import SchedulerService

JETZT = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


class _FakeDb:
    """Beantwortet beide Abfrageformen, die der Feuerpfad stellt."""

    def __init__(self, agent):
        self.agent = agent

    async def execute(self, _stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = self.agent
        res.scalars.return_value.all.return_value = []
        return res

    async def commit(self):
        pass


class _FakeRedis:
    def __init__(self):
        self.client = MagicMock()

    async def get_queue_depth(self, _agent_id):
        return 0

    async def get_agent_status(self, _agent_id):
        return {"current_task": ""}

    async def acquire_dispatch_lock(self, _agent_id, ttl_seconds=20):
        return "tok"

    async def release_dispatch_lock(self, agent_id, token):
        return None


class DerLaufErfaehrtDassErMeldenMuss(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = Agent(id="a1", name="Beobachter", state=AgentState.RUNNING, config={})
        self.svc = SchedulerService(redis=_FakeRedis())
        self.svc._stale_task_count = AsyncMock(return_value=0)
        self.router = SimpleNamespace(
            create_and_route_task=AsyncMock(return_value=SimpleNamespace(id="t1"))
        )

    def _zeitplan(self, **felder) -> Schedule:
        werte = dict(id="s1", name="PR-Beobachter", prompt="Offene PRs abarbeiten",
                     interval_seconds=1800, agent_id="a1", next_run_at=JETZT,
                     enabled=True, total_runs=0, success_count=0, fail_count=0)
        werte.update(felder)
        return Schedule(**werte)

    async def _feuern(self, schedule):
        await self.svc._execute_schedule(_FakeDb(self.agent), self.router, schedule, JETZT)
        return self.router.create_and_route_task.await_args.kwargs

    async def test_adaptiver_lauf_bekommt_den_melde_auftrag(self):
        aufruf = await self._feuern(self._zeitplan(adaptive=True, basis_intervall_s=1800))
        self.assertIn("report_run", aufruf["prompt"])
        self.assertTrue(aufruf["prompt"].endswith(MELDE_HINWEIS))
        self.assertIn("Offene PRs abarbeiten", aufruf["prompt"])
        for ergebnis in ("worked", "idle", "waiting"):
            self.assertIn(ergebnis, aufruf["prompt"])

    async def test_die_auftrags_metadaten_tragen_es_mit(self):
        aufruf = await self._feuern(self._zeitplan(adaptive=True, basis_intervall_s=1800))
        self.assertEqual(aufruf["metadata"], {"schedule_id": "s1", "adaptive": True})

    async def test_gegenprobe_ein_gewoehnlicher_lauf_bleibt_unveraendert(self):
        aufruf = await self._feuern(self._zeitplan(adaptive=False))
        # Jeder wiederkehrende Lauf traegt den Warte-Hinweis, nur der Melde-Auftrag fehlt.
        self.assertEqual(aufruf["prompt"], "Offene PRs abarbeiten" + WARTE_HINWEIS)
        self.assertNotIn("report_run", aufruf["prompt"])
        self.assertEqual(aufruf["metadata"], {"schedule_id": "s1", "adaptive": False})


class DerAbgleichUndDerTaktStreitenNicht(unittest.IsolatedAsyncioTestCase):
    """``eigeninitiative.abgleichen`` setzt das Intervall eines
    ``[Proactive]``-Zeitplans aus der Konfiguration. Die API lässt adaptiv auf
    solchen Zeitplänen gar nicht erst zu; käme der Schalter trotzdem dorthin
    (Direktschreiben in die Datenbank, Altbestand), dürfen sich die beiden
    nicht gegenseitig überschreiben."""

    def _plan(self, **felder) -> Schedule:
        werte = dict(id="p1", name="[Proactive] Beobachter", prompt="p",
                     interval_seconds=3600, agent_id="a1", enabled=True,
                     next_run_at=JETZT, total_runs=0, success_count=0, fail_count=0)
        werte.update(felder)
        return Schedule(**werte)

    def test_abweichung_misst_bei_adaptiv_den_grundtakt(self):
        # Adaptiv, Grundtakt == Konfiguration, aber gerade auf Stufe 3:
        # Das ist KEINE Abweichung, sonst würde bei jedem Abgleich zurückgesetzt.
        adaptiv = self._plan(adaptive=True, basis_intervall_s=3600, interval_seconds=28800)
        self.assertFalse(_takt_abweicht(adaptiv, 3600))
        self.assertTrue(_takt_abweicht(adaptiv, 1800))

    def test_gegenprobe_ohne_adaptiv_zaehlt_wie_bisher_das_intervall(self):
        fest = self._plan(adaptive=False, interval_seconds=28800)
        self.assertTrue(_takt_abweicht(fest, 3600))
        self.assertFalse(_takt_abweicht(self._plan(interval_seconds=3600), 3600))

    async def test_der_abgleich_setzt_bei_adaptiv_den_grundtakt_statt_der_stufe(self):
        """Vorher riss der Abgleich den Zeitplan bei jedem Lauf auf 3600 zurück."""
        plan = self._plan(adaptive=True, basis_intervall_s=1800, interval_seconds=28800,
                          leerlaeufe=5, stufe=3)
        agent = SimpleNamespace(
            id="a1", name="Beobachter",
            config={"proactive": {"enabled": True, "interval_seconds": 3600,
                                  "responsibilities": [{"titel": "PRs"}]}},
        )
        await abgleichen(_AbgleichDb([plan]), agent, hinweis=False, now=JETZT)
        self.assertEqual(plan.basis_intervall_s, 3600)
        self.assertEqual(plan.interval_seconds, 28800)   # Stufe bleibt stehen
        self.assertEqual((plan.leerlaeufe, plan.stufe), (5, 3))


class _AbgleichDb:
    """So viel Datenbank, wie ``abgleichen`` anfasst."""

    def __init__(self, zeitplaene):
        self.zeitplaene = list(zeitplaene)
        self.geloescht: list = []
        self.neu: list = []

    async def execute(self, _stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = self.zeitplaene
        return res

    async def delete(self, obj):
        self.geloescht.append(obj)

    def add(self, obj):
        self.neu.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass


class DieSelbstheilungErbtDieMeldungNicht(unittest.TestCase):
    """``_schedule_retry`` kopiert die Metadaten des gescheiterten Laufs. Blieben
    ``takt_wechsel``/``lauf_ergebnis`` darin, gälte die eigene Meldung des
    Wiederholungslaufs als Dublette: Der geheilte Lauf fiele still aus der
    Taktführung heraus, und der Takt bliebe auf der Stufe des gescheiterten
    Versuchs stehen."""

    def test_die_beiden_lauf_schluessel_werden_entfernt(self):
        import inspect

        from app.core.task_router import TaskRouter
        quelle = inspect.getsource(TaskRouter._schedule_retry)
        kopf = quelle[:quelle.index("retry = Task(")]
        self.assertIn('meta.pop("takt_wechsel", None)', kopf)
        self.assertIn('meta.pop("lauf_ergebnis", None)', kopf)

    def test_der_wiederholungslauf_darf_danach_melden(self):
        """Verhalten statt Quelltext: Mit bereinigten Metadaten erkennt
        ``schon_gemeldet`` keine Dublette mehr."""
        from app.services.takt_service import schon_gemeldet
        gescheitert = SimpleNamespace(metadata_={
            "schedule_id": "s1", "lauf_ergebnis": "idle",
            "takt_wechsel": {"von_s": 1800, "auf_s": 7200, "adaptive": True},
        })
        self.assertIsNotNone(schon_gemeldet(gescheitert, "idle"))
        geerbt = dict(gescheitert.metadata_)
        geerbt.pop("takt_wechsel")
        geerbt.pop("lauf_ergebnis")
        self.assertIsNone(schon_gemeldet(SimpleNamespace(metadata_=geerbt), "idle"))


if __name__ == "__main__":
    unittest.main()
