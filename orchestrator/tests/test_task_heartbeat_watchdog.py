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
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models.task import TaskStatus
from app.services import watchdog

_WURZEL = Path(__file__).resolve().parents[2]
_CONSUMER = (_WURZEL / "agent" / "app" / "task_consumer.py").read_text()
_ROUTER = (_WURZEL / "orchestrator" / "app" / "core" / "task_router.py").read_text()
_MAIN = (_WURZEL / "orchestrator" / "app" / "main.py").read_text()
_REDIS = (_WURZEL / "orchestrator" / "app" / "services" / "redis_service.py").read_text()
_SCHED = (_WURZEL / "orchestrator" / "app" / "services" / "scheduler_service.py").read_text()


class _Aufgabe:
    def __init__(self, status, updated_at):
        self.status = status
        self.updated_at = updated_at
        self.title = "Test"
        self.metadata_ = {}
        self.error = None
        self.completed_at = None


class DerAgentSendetEinLebenszeichenTests(unittest.TestCase):
    def test_es_gibt_eine_herzschlag_schleife(self):
        self.assertIn("async def _herzschlag(self", _CONSUMER)
        self.assertIn('"task:heartbeat"', _CONSUMER)

    def test_sie_laeuft_neben_der_aufgabe(self):
        """Sequenziell gesendet waere sie waehrend der Arbeit still — genau der
        Zustand, den sie beheben soll."""
        self.assertIn("asyncio.create_task(self._herzschlag(task_id))", _CONSUMER)

    def test_sie_wird_am_ende_beendet(self):
        """Sonst bliebe je Aufgabe eine Schleife fuer immer stehen."""
        block = _CONSUMER.split("finally:", 1)[1][:300]
        self.assertIn("herzschlag.cancel()", block)

    def test_der_takt_liegt_deutlich_unter_der_schwelle(self):
        """Ein einzelner verpasster Schlag darf keinen Abbruch ausloesen."""
        self.assertIn("HERZSCHLAG_SEKUNDEN = 60", _CONSUMER)

    def test_ein_fehlschlag_reisst_die_aufgabe_nicht_mit(self):
        block = _CONSUMER.split("async def _herzschlag", 1)[1][:1200]
        self.assertIn("except asyncio.CancelledError:", block)
        self.assertIn("except Exception", block)


class DerOrchestratorNimmtEsEntgegenTests(unittest.TestCase):
    def test_der_kanal_ist_erlaubt(self):
        """Ohne Eintrag in der Kanalliste sperrt die Redis-ACL ihn aus."""
        self.assertIn('"task:heartbeat"', _REDIS)

    def test_er_wird_abonniert(self):
        self.assertIn('await pubsub.subscribe("task:heartbeat")', _MAIN)

    def test_und_einem_handler_zugeordnet(self):
        self.assertIn('elif channel == "task:heartbeat":', _MAIN)
        self.assertIn("handle_task_heartbeat(data)", _MAIN)

    def test_der_handler_schiebt_die_zeile_weiter(self):
        block = _ROUTER.split("async def handle_task_heartbeat", 1)[1][:1400]
        self.assertIn("task.updated_at = datetime.now(timezone.utc)", block)
        self.assertIn("await self.db.commit()", block)

    def test_nur_fuer_eine_laufende_aufgabe(self):
        """Ein spaeter Schlag darf eine bereits beendete Aufgabe nicht
        wiederbeleben."""
        block = _ROUTER.split("async def handle_task_heartbeat", 1)[1][:1400]
        self.assertIn("task.status != TaskStatus.RUNNING", block)

    def test_die_vorhandene_spalte_wird_endlich_gefuettert(self):
        """`job_state.last_heartbeat` gab es laengst — gefuettert hat sie nie
        jemand (genau der Befund aus #692)."""
        block = _ROUTER.split("async def handle_task_heartbeat", 1)[1][:2000]
        self.assertIn('checkpoint(self.db, f"task:{task_id}"', block)


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


class DerAgentWirdWirklichGestopptTests(unittest.TestCase):
    def test_beim_abraeumen_wird_abgebrochen(self):
        """Sonst arbeitet der Agent nach dem Abbruch weiter und verbrennt Zeit
        und Token fuer ein Ergebnis, das niemand mehr annimmt (#692 Punkt C)."""
        block = _SCHED.split("async def _tick_stale_task_watchdog", 1)[1][:4000]
        self.assertIn('f"agent:{task.agent_id}:task:cancel"', block)

    def test_die_nutzlast_passt_zum_zuhoerer(self):
        """Der Zuhoerer im Agenten liest die Nutzlast als rohe Kennung — JSON
        haelt er fuer eine unbekannte Aufgabe und stoppt nichts."""
        block = _SCHED.split("async def _tick_stale_task_watchdog", 1)[1][:4000]
        stelle = block.index('task:cancel"')
        self.assertIn("task.id", block[stelle:stelle + 120])
        self.assertNotIn('_json.dumps({"task_id"', block[stelle:stelle + 200])


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
