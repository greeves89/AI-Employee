"""Der Waechter darf keine gesunde Aufgabe mehr abraeumen.

Befund #692: `is_task_stale` prueft, wie lange an einer laufenden Aufgabe nichts
mehr geschrieben wurde. Zwischen `task:started` und `task:completions` schrieb
aber NICHTS an der Zeile — der Waechter mass damit nicht die Gesundheit des
Arbeiters, sondern die verstrichene Zeit. Er war faktisch eine harte
30-Minuten-Obergrenze fuer jede delegierte Aufgabe, und meldete den Abbruch als
„Worker still gestorben", was jede Fehlersuche in die falsche Richtung schickte.

Beleg (31.08.2026, vier parallel delegierte Reviews): Dauern von 30.3, 30.3,
30.3 und 30.4 Minuten, drei davon mit identischem `completed_at` — der
Fingerabdruck einer Zeitschwelle, nicht eines gemeinsamen Ausfalls.

Seit #726 wird die Orchestrator-Seite am VERHALTEN geprueft: der Handler mit
einer Datenbank-Attrappe, der Waechter-Tick mit gestellten Aufgaben und einem
Redis-Doppel, das festhaelt, was auf welchem Kanal gesendet wurde. Die
Agenten-Seite (die Herzschlag-Schleife selbst) liegt in
``agent/tests/test_task_heartbeat_loop.py``.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.task_router import TaskRouter
from app.models.task import TaskStatus
from app.services import scheduler_service
from app.services import watchdog
from app.services.redis_service import _AGENT_ACL_CHANNEL_PATTERNS
from app.services.scheduler_service import SchedulerService

_WURZEL = Path(__file__).resolve().parents[2]
_MAIN = (_WURZEL / "orchestrator" / "app" / "main.py").read_text()
_SCHED = (_WURZEL / "orchestrator" / "app" / "services" / "scheduler_service.py").read_text()


class _Aufgabe:
    def __init__(self, status, updated_at, task_id="t-1", agent_id="agent-7"):
        self.id = task_id
        self.agent_id = agent_id
        self.status = status
        self.updated_at = updated_at
        self.title = "Test"
        self.metadata_ = {}
        self.error = None
        self.completed_at = None


class DerOrchestratorNimmtEsEntgegenTests(unittest.IsolatedAsyncioTestCase):
    def test_der_kanal_ist_erlaubt(self):
        """Ohne Eintrag in der Kanalliste sperrt die Redis-ACL ihn aus."""
        self.assertIn("task:heartbeat", _AGENT_ACL_CHANNEL_PATTERNS)

    def test_er_wird_abonniert(self):
        self.assertIn('await pubsub.subscribe("task:heartbeat")', _MAIN)

    def test_und_einem_handler_zugeordnet(self):
        self.assertIn('elif channel == "task:heartbeat":', _MAIN)
        self.assertIn("handle_task_heartbeat(data)", _MAIN)

    def _router(self, task):
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = task
        db.execute = AsyncMock(return_value=result)
        return TaskRouter(db=db, redis=MagicMock(), load_balancer=MagicMock()), db

    async def test_der_handler_schiebt_die_zeile_weiter(self):
        alt = datetime.now(timezone.utc) - timedelta(hours=2)
        aufgabe = _Aufgabe(TaskStatus.RUNNING, alt)
        router, db = self._router(aufgabe)
        with patch("app.services.job_state.checkpoint", AsyncMock()):
            await router.handle_task_heartbeat({"task_id": "t-1"})
        self.assertGreater(aufgabe.updated_at, alt + timedelta(hours=1))
        db.commit.assert_awaited()

    async def test_nur_fuer_eine_laufende_aufgabe(self):
        """Ein spaeter Schlag darf eine bereits beendete Aufgabe nicht
        wiederbeleben."""
        alt = datetime.now(timezone.utc) - timedelta(hours=2)
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.QUEUED):
            with self.subTest(status=status):
                aufgabe = _Aufgabe(status, alt)
                router, db = self._router(aufgabe)
                with patch("app.services.job_state.checkpoint", AsyncMock()) as cp:
                    await router.handle_task_heartbeat({"task_id": "t-1"})
                self.assertEqual(aufgabe.updated_at, alt)
                db.commit.assert_not_awaited()
                cp.assert_not_awaited()

    async def test_ohne_kennung_wird_nicht_einmal_gesucht(self):
        router, db = self._router(None)
        await router.handle_task_heartbeat({})
        db.execute.assert_not_awaited()

    async def test_die_vorhandene_spalte_wird_endlich_gefuettert(self):
        """`job_state.last_heartbeat` gab es laengst — gefuettert hat sie nie
        jemand (genau der Befund aus #692)."""
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc))
        router, db = self._router(aufgabe)
        with patch("app.services.job_state.checkpoint", AsyncMock()) as cp:
            await router.handle_task_heartbeat({"task_id": "t-1"})
        cp.assert_awaited_once_with(db, "task:t-1", kind="agent_task", ref_id="t-1")

    async def test_ein_fehler_in_der_spalte_reisst_das_lebenszeichen_nicht_mit(self):
        """Die Zeile ist dann schon fortgeschrieben — das ist das Wichtigere."""
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc) - timedelta(hours=1))
        router, db = self._router(aufgabe)
        with patch("app.services.job_state.checkpoint", AsyncMock(side_effect=RuntimeError("kaputt"))):
            await router.handle_task_heartbeat({"task_id": "t-1"})   # darf nicht werfen
        db.commit.assert_awaited()


class DieSchwelleIstEinstellbarTests(unittest.TestCase):
    def test_der_waechter_liest_sie_aus_der_einstellung(self):
        self.assertIn("watchdog_stale_task_minutes", _SCHED)

    def test_die_meldung_nennt_die_wirkliche_schwelle(self):
        """Fest verdrahtete „30min" wuerden bei angehobener Schwelle luegen."""
        self.assertIn("minuten = int(schwelle.total_seconds() // 60)", _SCHED)
        self.assertNotIn("seit über 30min kein", _SCHED)

    def test_mark_task_stale_nimmt_die_schwelle_entgegen(self):
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc))
        watchdog.mark_task_stale(aufgabe, datetime.now(timezone.utc), timedelta(minutes=180))
        self.assertIn("180", aufgabe.error)

    def test_ohne_angabe_bleibt_es_beim_alten_wert(self):
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc))
        watchdog.mark_task_stale(aufgabe, datetime.now(timezone.utc))
        self.assertIn("30", aufgabe.error)


class _Db:
    def __init__(self):
        self.hinzugefuegt = []
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.hinzugefuegt.append(obj)

    async def commit(self):
        self.committed += 1


class DerAgentWirdWirklichGestopptTests(unittest.IsolatedAsyncioTestCase):
    """Sonst arbeitet der Agent nach dem Abbruch weiter und verbrennt Zeit
    und Token fuer ein Ergebnis, das niemand mehr annimmt (#692 Punkt C)."""

    def _tick(self, stale):
        redis = SimpleNamespace(client=SimpleNamespace(publish=AsyncMock()))
        svc = SchedulerService(redis=redis)
        db = _Db()
        patches = [
            patch.object(scheduler_service, "resilient_session", lambda: db),
            patch.object(scheduler_service, "find_stale_tasks", AsyncMock(return_value=stale)),
        ]
        return svc, db, redis.client.publish, patches

    async def _laufen(self, stale):
        svc, db, publish, patches = self._tick(stale)
        for p in patches:
            p.start()
        try:
            await svc._tick_stale_task_watchdog()
        finally:
            for p in patches:
                p.stop()
        return db, publish

    async def test_beim_abraeumen_wird_abgebrochen(self):
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc) - timedelta(hours=4),
                           task_id="t-stale", agent_id="agent-7")
        db, publish = await self._laufen([aufgabe])
        kanaele = {c.args[0] for c in publish.await_args_list}
        self.assertIn("agent:agent-7:task:cancel", kanaele)
        # Und die Aufgabe selbst gilt als gescheitert, nicht als laufend.
        self.assertEqual(aufgabe.status, TaskStatus.FAILED)
        self.assertTrue(aufgabe.metadata_.get("stale"))
        self.assertEqual(db.committed, 1)
        self.assertEqual(len(db.hinzugefuegt), 1)     # die Benachrichtigung

    async def test_die_nutzlast_passt_zum_zuhoerer(self):
        """Der Zuhoerer im Agenten liest die Nutzlast als rohe Kennung — JSON
        haelt er fuer eine unbekannte Aufgabe und stoppt nichts."""
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc) - timedelta(hours=4),
                           task_id="t-stale", agent_id="agent-7")
        _, publish = await self._laufen([aufgabe])
        nutzlast = [c.args[1] for c in publish.await_args_list if c.args[0] == "agent:agent-7:task:cancel"]
        self.assertEqual(nutzlast, ["t-stale"])
        with self.assertRaises(ValueError):
            json.loads(nutzlast[0])                  # kein JSON, eine Kennung

    async def test_ohne_agent_wird_niemand_gerufen(self):
        aufgabe = _Aufgabe(TaskStatus.RUNNING, datetime.now(timezone.utc) - timedelta(hours=4),
                           task_id="t-verwaist", agent_id=None)
        _, publish = await self._laufen([aufgabe])
        kanaele = [c.args[0] for c in publish.await_args_list]
        self.assertNotIn("agent:None:task:cancel", kanaele)
        self.assertFalse(any(k.endswith(":task:cancel") for k in kanaele))
        self.assertEqual(aufgabe.status, TaskStatus.FAILED)

    async def test_ohne_verstummte_passiert_nichts(self):
        db, publish = await self._laufen([])
        publish.assert_not_awaited()
        self.assertEqual(db.committed, 0)


class DieAlteFehldiagnoseStehtNichtMehrDaTests(unittest.TestCase):
    QUELLE = (_WURZEL / "orchestrator" / "app" / "services" / "watchdog.py").read_text()

    def test_die_falsche_behauptung_ist_weg(self):
        """Der Kommentar behauptete, jede laufende Aufgabe schiebe `updated_at`
        weiter. Genau diese Annahme hat die Fehlersuche verzoegert."""
        self.assertNotIn("bumps updated_at (TimestampMixin onupdate) on every status/step",
                         self.QUELLE)

    def test_und_der_wahre_hergang_steht_dort(self):
        self.assertIn("#692", self.QUELLE)
        self.assertIn("task:heartbeat", self.QUELLE)


class DieErkennungSelbstBleibtRichtigTests(unittest.TestCase):
    def test_eine_frisch_geschlagene_aufgabe_lebt(self):
        jetzt = datetime.now(timezone.utc)
        aufgabe = _Aufgabe(TaskStatus.RUNNING, jetzt - timedelta(minutes=1))
        self.assertFalse(watchdog.is_task_stale(aufgabe, jetzt, timedelta(minutes=30)))

    def test_eine_wirklich_verstummte_wird_erkannt(self):
        jetzt = datetime.now(timezone.utc)
        aufgabe = _Aufgabe(TaskStatus.RUNNING, jetzt - timedelta(hours=4))
        self.assertTrue(watchdog.is_task_stale(aufgabe, jetzt, timedelta(minutes=180)))

    def test_eine_lange_laufende_mit_herzschlag_ueberlebt(self):
        """Der Kern des Fehlers: vier Stunden Arbeit, aber vor einer Minute noch
        ein Lebenszeichen — frueher tot, jetzt gesund."""
        jetzt = datetime.now(timezone.utc)
        aufgabe = _Aufgabe(TaskStatus.RUNNING, jetzt - timedelta(minutes=1))
        self.assertFalse(watchdog.is_task_stale(aufgabe, jetzt, timedelta(minutes=30)))

    def test_nur_laufende_aufgaben(self):
        jetzt = datetime.now(timezone.utc)
        for status in (TaskStatus.QUEUED, TaskStatus.COMPLETED, TaskStatus.FAILED):
            aufgabe = _Aufgabe(status, jetzt - timedelta(days=2))
            self.assertFalse(watchdog.is_task_stale(aufgabe, jetzt))


if __name__ == "__main__":
    unittest.main()
