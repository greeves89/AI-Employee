"""Ein gestoppter Agent wird nicht proaktiv angesteuert.

Befund beim Kunden (2026-08-07): zwei gestoppte Agenten haben ueber vier Wochen
**337 Laeufe** produziert, jeden einzelnen davon fehlgeschlagen — der Zeitplan feuerte
stuendlich weiter, obwohl niemand da war, der ihn haette ausfuehren koennen. Aufgefallen
ist es niemandem.

Der Test prueft die Regel am Quelltext (die Scheduler-Schleife zieht die halbe API-Schicht
samt Docker-Abhaengigkeit mit) und ist bewusst so geschrieben, dass er faellt, sobald die
Pruefung wieder aus dem Feuer-Pfad verschwindet.
"""

import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/services/scheduler_service.py").read_text()
# Nur der Abschnitt, der einen faelligen Zeitplan ausfuehrt.
FIRE = SRC.split("Create a task from a schedule and advance next_run_at", 1)[1].split(
    "async def _proactive_config", 1
)[0]


class StoppedAgentTests(unittest.TestCase):
    def test_state_is_checked_before_firing(self):
        """Der Zustand wird ueber den gemeinsamen Dienstzustand geprueft
        (``core/agent_duty``), damit 'arbeitsfaehig' ueberall dasselbe heisst."""
        self.assertIn("agent_duty.assess(", FIRE)
        self.assertIn('duty["state"] != agent_duty.OK', FIRE)

    def test_only_live_states_are_driven(self):
        """RUNNING, IDLE und WORKING duerfen laufen — alles andere nicht.

        IDLE gehoert dazu: ein leerlaufender Agent ist ansprechbar, und genau ihn will
        der proaktive Lauf ja aufwecken. Die Liste steht in core/agent_duty.
        """
        from pathlib import Path as _P
        core = (_P(__file__).resolve().parents[1] / "app/core/agent_duty.py").read_text()
        self.assertIn('_LIVE_STATES = ("running", "idle", "working")', core)

    def test_schedule_keeps_its_rhythm_instead_of_piling_up(self):
        """Uebersprungen heisst weiterruecken — sonst laeuft next_run_at in die
        Vergangenheit und der Agent bekommt beim Start einen Schwall Nachholarbeit."""
        skip_block = FIRE.split("is STOPPED", 1)[0] if "is STOPPED" in FIRE else FIRE
        self.assertIn("schedule.next_run_at = _calc_next_run(schedule, now)", skip_block)

    def test_the_skip_is_logged(self):
        """Stilles Ueberspringen waere derselbe Fehler nochmal — nur leiser."""
        self.assertIn("uebersprungen — Agent", FIRE)

    def test_check_runs_before_the_busy_check(self):
        """Zuerst 'lebt der Agent ueberhaupt', dann 'ist er beschaeftigt' — die
        Beschaeftigt-Pruefung fragt Redis und liefert fuer einen toten Agenten nichts
        Brauchbares. (Der Dienstzustand liest die Warteschlange selbst mit, deshalb
        steht die ALTE Beschaeftigt-Pruefung dahinter.)"""
        self.assertLess(FIRE.index("agent_duty.assess("), FIRE.index("is_busy_with_task"))

    def test_failure_triggers_a_handover(self):
        """Neu: ein Ausfall bleibt nicht bei 'uebersprungen' stehen — die Arbeit muss
        jemand uebernehmen, sonst faellt es wieder niemandem auf."""
        self.assertIn("duty_service.escalate_failure", FIRE)


if __name__ == "__main__":
    unittest.main()
