"""„Abbrechen" muss abbrechen — und wenn nicht, muss es das sagen.

Nutzerbericht vom 21.08.2026, mit vollstaendigem Gespraechsprotokoll: der Nutzer
sagte DREIMAL „abbrechen", die Stimme antwortete dreimal „Beide Aufgaben wurden
gestoppt" — und die Aufgabe lief Stunden spaeter immer noch:

    tu7hsco5e | Analyse der Excel-Testrechnung | RUNNING | start 10:18 | ende None

Vier Schichten desselben Problems, alle belegt:

1. Die Sprachfront meldete Erfolg, sobald ein Redis-``publish`` ohne Fehler
   zurueckkam. Ein publish gelingt aber auch, wenn NIEMAND zuhoert.
2. Sie kannte nur ``self._planned`` — Aufgaben aus DIESER Sitzung. Das Gespraech
   war fortgesetzt, die Menge also leer.
3. ``TaskRouter.cancel_task`` wies laufende Aufgaben mit einem Fehler ab.
4. Der Kanal ``agent:{id}:task:cancel`` wurde seit jeher besendet — und hatte
   keinen einzigen Zuhoerer.
"""

import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
ROUTER = (WURZEL / "orchestrator/app/core/task_router.py").read_text()
VOICE = (WURZEL / "orchestrator/app/services/realtime_voice_session.py").read_text()
CONSUMER = (WURZEL / "agent/app/task_consumer.py").read_text()
SEITE = (WURZEL / "frontend/src/app/tasks/page.tsx").read_text()


class ARunningTaskCanBeStoppedTests(unittest.TestCase):
    def test_the_router_no_longer_refuses_running_tasks(self):
        block = ROUTER.split("async def cancel_task", 1)[1][:1800]
        self.assertIn("TaskStatus.RUNNING", block)
        # Abgelehnt wird nur noch, was wirklich vorbei ist.
        self.assertIn("TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED", block)

    def test_it_signals_the_agent_for_a_running_task(self):
        block = ROUTER.split("async def cancel_task", 1)[1][:1800]
        self.assertIn("task:cancel", block)

    def test_the_agent_now_listens_on_that_channel(self):
        """Der Kanal existierte, aber niemand hoerte zu — das war der Kern."""
        self.assertIn("_cancel_listener", CONSUMER)
        block = CONSUMER.split("async def _cancel_listener", 1)[1][:1800]
        self.assertIn('f"agent:{self.agent_id}:task:cancel"', block)

    def test_the_listener_runs_alongside_the_queue(self):
        """Liefe er in derselben Schleife, kaeme er erst dran, wenn gerade
        keine Aufgabe verarbeitet wird — also genau dann nicht, wenn man ihn
        braucht."""
        self.assertIn("asyncio.create_task(self._cancel_listener())", CONSUMER)

    def test_it_can_stop_one_task_not_only_everything(self):
        self.assertIn("_runner_by_task", CONSUMER)
        block = CONSUMER.split("async def _cancel_listener", 1)[1][:1800]
        self.assertIn("runner.interrupt()", block)

    def test_the_mapping_is_cleaned_up_afterwards(self):
        """Sonst waechst sie mit jeder Aufgabe und zeigt auf tote Runner."""
        self.assertIn("self._runner_by_task.pop(task_id, None)", CONSUMER)


class TheVoiceTellsTheTruthTests(unittest.TestCase):
    def test_it_no_longer_reports_success_from_a_bare_publish(self):
        """Das war die Luege: `publish` gelingt auch ohne Zuhoerer."""
        block = VOICE.split("async def _cancel_task", 1)[1][:3200]
        self.assertNotIn("stopped = True", block)

    def test_it_looks_at_all_open_tasks_not_only_this_session(self):
        block = VOICE.split("async def _cancel_task", 1)[1][:3200]
        self.assertIn("Task.agent_id == self.agent_id", block)
        self.assertIn("Task.status.in_(OFFEN)", block)

    def test_it_checks_again_afterwards(self):
        """Der eigentliche Fix: nachsehen statt behaupten."""
        block = VOICE.split("async def _cancel_task", 1)[1][:3200]
        self.assertIn("uebrig = await _offene()", block)

    def test_it_says_so_when_something_survived(self):
        block = VOICE.split("async def _cancel_task", 1)[1][:3200]
        self.assertIn("läuft/laufen noch", block)

    def test_it_names_what_is_still_running(self):
        """„Etwas laeuft noch" ohne Namen zwingt zur naechsten Rueckfrage."""
        block = VOICE.split("async def _cancel_task", 1)[1][:3200]
        self.assertIn("namen", block)


class TheUiHasAManualStopTests(unittest.TestCase):
    """Ausdruecklicher Wunsch: „ich will bei aufgaben auch noch einen Manuellen
    stop haben"."""

    def test_a_running_task_can_be_stopped_from_the_list(self):
        self.assertIn('const laeuft = task.status === "running"', SEITE)
        self.assertIn("const canCancel = laeuft ||", SEITE)

    def test_the_stop_button_is_visible_without_hovering(self):
        """Wer eine laufende Aufgabe stoppen will, sucht den Knopf sofort —
        nicht erst, wenn er zufaellig darueberfaehrt."""
        block = SEITE.split("{canCancel && (", 1)[1][:1400]
        self.assertIn("laeuft", block)
        # Der Verstecken-Stil gilt nur noch fuer wartende Aufgaben.
        vor_dem_doppelpunkt = block.split("opacity-0 group-hover:opacity-100")[0]
        self.assertIn("?", vor_dem_doppelpunkt)

    def test_the_words_distinguish_the_two_cases(self):
        """Eine wartende Aufgabe nimmt man aus der Schlange, eine laufende
        unterbricht man — das sind zwei verschiedene Dinge."""
        self.assertIn('"Stoppen"', SEITE)
        self.assertIn('"Abbrechen"', SEITE)


if __name__ == "__main__":
    unittest.main()
