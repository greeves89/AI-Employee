"""Eine vergangene Startzeit darf nie ein sofortiges Feuern ausloesen (#642).

Am 2026-08-23 bekam ein Agent zweimal den Auftrag "Arbeite ihn JETZT ab" fuer
Bloecke, die im Titel bereits "ERLEDIGT" trugen: der neu geschriebene Tagesplan
legte fuer JEDEN Block mit Uhrzeit einen aktivierten Einmal-Zeitplan an — auch
wenn die Uhrzeit laengst vorbei war. Der Scheduler sah next_run_at in der
Vergangenheit und feuerte sofort.
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.core.day_plan_store import _PAST_START_GRACE, _start_is_past


class PastStartTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)

    def test_clearly_past_is_past(self):
        self.assertTrue(_start_is_past(self.now - timedelta(hours=1), self.now))

    def test_the_future_is_not_past(self):
        self.assertFalse(_start_is_past(self.now + timedelta(minutes=1), self.now))

    def test_just_now_falls_within_the_grace(self):
        """Uhr-Drift oder langsames Schreiben: "gerade eben" zaehlt noch."""
        self.assertFalse(_start_is_past(self.now - _PAST_START_GRACE + timedelta(seconds=30), self.now))

    def test_naive_timestamps_are_treated_as_utc(self):
        naive_past = (self.now - timedelta(hours=2)).replace(tzinfo=None)
        self.assertTrue(_start_is_past(naive_past, self.now))


class WiringTests(unittest.TestCase):
    """replace_plan und sync_block_schedule muessen den Schutz beide anwenden."""

    def test_replace_plan_skips_schedules_for_past_starts(self):
        import inspect
        from app.core import day_plan_store
        src = inspect.getsource(day_plan_store.replace_plan)
        self.assertIn("_start_is_past(row.planned_start)", src)
        # Der Schutz muss VOR dem Anlegen des Zeitplans greifen.
        self.assertLess(src.index("_start_is_past"), src.index("schedule_id = uuid"))

    def test_sync_disables_the_trigger_when_moved_into_the_past(self):
        import inspect
        from app.core import day_plan_store
        src = inspect.getsource(day_plan_store.sync_block_schedule)
        self.assertIn("_start_is_past(row.planned_start)", src)
        self.assertIn("schedule.enabled = False", src)


if __name__ == "__main__":
    unittest.main()
