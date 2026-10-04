"""Der Takt eines Zeitplans kommt lesbar vom Server.

Die Oberflaeche rechnete ihn selbst aus ``interval_seconds``. Cron-Zeitplaene
haben dort 0 — angezeigt wurde „Every 0 min", bei jedem Agenten zweimal
(Abendplanung, Morgencheck).
"""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.schemas.schedule import ScheduleResponse


def _zeitplan(**kw):
    jetzt = datetime.now(timezone.utc)
    basis = dict(
        id="s1", name="[Rhythmus] Abendplanung", prompt="p", interval_seconds=0,
        cron_expression=None, timezone="Europe/Berlin", priority=0, agent_id=None,
        model=None, enabled=True, next_run_at=jetzt, last_run_at=None, total_runs=0,
        success_count=0, fail_count=0, created_at=jetzt, updated_at=jetzt,
    )
    basis.update(kw)
    return SimpleNamespace(**basis)


class TaktTest(unittest.TestCase):
    def test_cron_werktags(self):
        r = ScheduleResponse.from_schedule(_zeitplan(cron_expression="30 17 * * 1-5"))
        self.assertEqual(r.takt, "Mo–Fr 17:30")

    def test_cron_taeglich(self):
        r = ScheduleResponse.from_schedule(_zeitplan(cron_expression="0 9 * * *"))
        self.assertEqual(r.takt, "täglich 09:00")

    def test_intervall(self):
        r = ScheduleResponse.from_schedule(_zeitplan(interval_seconds=3600))
        self.assertEqual(r.takt, "alle 1 Std")

    def test_monatlich(self):
        """#901: Das Formular bietet „monatlich am 15. um 09:00" an."""
        r = ScheduleResponse.from_schedule(_zeitplan(cron_expression="0 9 15 * *"))
        self.assertEqual(r.takt, "monatlich am 15. um 09:00")

    def test_woechentlich_mehrere_tage(self):
        r = ScheduleResponse.from_schedule(_zeitplan(cron_expression="30 7 * * 1,3"))
        self.assertEqual(r.takt, "Mo, Mi 07:30")

    def test_nie_null_minuten(self):
        """Der alte Fehler: ein Cron-Zeitplan darf nie als Null-Intervall erscheinen."""
        r = ScheduleResponse.from_schedule(_zeitplan(cron_expression="*/15 * * * *"))
        self.assertNotIn("0 Min", r.takt)
        self.assertTrue(r.takt)


if __name__ == "__main__":
    unittest.main()
