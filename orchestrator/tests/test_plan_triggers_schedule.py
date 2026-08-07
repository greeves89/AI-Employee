"""Ein Plan-Block ist eine Verabredung — und wird ausgeloest.

Der Nutzer sah seinen Tagesplan im Kalender stehen und fragte zu Recht: „wieso faengt der
nicht an?". Ein Block war reine Anzeige. Er wird jetzt ueber die Maschinerie ausgeloest,
die Zeitplaene seit jeher ausfuehrt — KEIN zweiter Ausloeser daneben.
"""

import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
STORE = (ORCH / "app/core/day_plan_store.py").read_text()
SCHED = (ORCH / "app/services/scheduler_service.py").read_text()


class PlanCreatesSchedulesTests(unittest.TestCase):
    def test_a_timed_block_gets_a_schedule(self):
        self.assertIn("db.add(Schedule(", STORE)
        self.assertIn('f"[Plan] {row.title[:60]}"', STORE)
        self.assertIn("next_run_at=row.planned_start", STORE)

    def test_it_is_a_one_shot(self):
        """Ein Block soll einmal laufen, nicht im Kreis."""
        self.assertIn("interval_seconds=0,", STORE)

    def test_block_without_a_time_stays_a_note(self):
        self.assertIn("if not row.planned_start:", STORE)
        self.assertIn("continue", STORE)

    def test_replanning_removes_the_old_schedules(self):
        """Sonst feuert ein gestrichener Block weiter."""
        self.assertIn("delete(Schedule).where(Schedule.id.in_(stale_ids))", STORE)

    def test_block_remembers_its_schedule(self):
        self.assertIn("row.schedule_id = schedule_id", STORE)
        self.assertIn("schedule_id", (ORCH / "app/models/agent_plan_item.py").read_text())


class OneShotSafetyTests(unittest.TestCase):
    def test_one_shot_disables_itself_after_firing(self):
        """Ohne das stuende next_run_at sofort wieder in der Vergangenheit — der Block
        feuerte im 30-Sekunden-Takt weiter."""
        tail = SCHED.split("# Advance schedule", 1)[1][:600]
        self.assertIn("schedule.interval_seconds == 0", tail)
        self.assertIn("schedule.enabled = False", tail)

    def test_no_second_dispatcher_exists(self):
        """Die Ausfuehrung laeuft ueber Zeitplaene — ein paralleler Ausloeser waere
        genau die Insellösung, die hier nicht sein soll."""
        self.assertNotIn("_dispatch_due_plan_items", SCHED)


class CardFlagTests(unittest.TestCase):
    def test_agent_list_reports_the_real_state(self):
        """Die Kachel meldete 'kein Auftrag', obwohl elf Bereiche hinterlegt waren:
        die Liste baut ihre Felder aus dem Metrik-Woerterbuch, nicht aus dem Antwortmodell."""
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn('"has_responsibilities": bool(', mgr)


if __name__ == "__main__":
    unittest.main()


class SelfHealingTests(unittest.TestCase):
    """Block mit Uhrzeit ⇒ Zeitplan. Als Invariante, nicht als einmalige Nachbesserung.

    Der erste Anlauf legte den Zeitplan nur beim Schreiben an — Bloecke aus aelteren
    Fassungen standen deshalb im Kalender und liefen nie. Genau das hat der Nutzer
    gesehen: „wieso macht der dann nichts?".
    """

    def test_orphan_blocks_are_armed_on_every_tick(self):
        self.assertIn("async def _arm_plan_blocks", SCHED)
        self.assertIn("AgentPlanItem.schedule_id.is_(None)", SCHED)
        self.assertIn("_arm_plan_blocks()", SCHED.split("async def _arm_plan_blocks", 1)[0])

    def test_only_future_days_and_still_planned(self):
        block = SCHED.split("async def _arm_plan_blocks", 1)[1].split("async def _stale_task_count", 1)[0]
        self.assertIn('AgentPlanItem.status == "planned"', block)
        self.assertIn("AgentPlanItem.plan_date >= _date.today()", block)

    def test_a_missed_time_is_caught_up_not_dropped(self):
        block = SCHED.split("async def _arm_plan_blocks", 1)[1].split("async def _stale_task_count", 1)[0]
        self.assertIn("next_run_at=item.planned_start", block)
