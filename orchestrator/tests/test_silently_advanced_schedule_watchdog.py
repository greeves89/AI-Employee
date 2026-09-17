"""Ein Cron-Zeitplan kann einen faelligen Slot lautlos verwerfen, ohne dass
irgendein bestehendes Signal es zeigt (Issue #720, Punkt 3).

Beobachtet an Zeitplan `61c2efc4` (taeglicher Bericht, cron `0 21 * * *`):
zwei Tage in Folge kein Task, keine Log-Zeile, `fail_count: 0`,
`success_rate: 1.0`, `next_run_at` sauber in der Zukunft. Ursache:
`_retry_or_advance` gibt einen Slot ohne Redis/Retry-Budget per
`_calc_next_run` auf — und der schiebt `next_run_at` GENAUSO in die Zukunft
wie ein echter, erfolgreicher Lauf. `is_schedule_missed` (der bestehende
Verpasst-Waechter) sucht ausdruecklich nach `next_run_at` in der Vergangenheit
und ist fuer diese Klasse damit strukturell blind.

Der einzige Verraeter ist `last_run_at`, das dem Cron-Plan hinterherhinkt.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.models.schedule import Schedule
from app.services.watchdog import (
    find_silently_advanced_schedules,
    is_schedule_silently_advanced,
)


def _mock_db(rows):
    db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=result)
    return db


_DEFAULT_NOW = datetime(2026, 9, 7, 21, 10, tzinfo=timezone.utc)
# Weit in der Vergangenheit: "existierte schon immer", fuer Tests, die die
# Anlage-Schranke nicht meinen.
_LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _cron_schedule(sid, cron, last_run_ago=None, enabled=True, tz="UTC", now=_DEFAULT_NOW,
                    created_at=_LONG_AGO):
    s = Schedule()
    s.id = sid
    s.name = f"Schedule {sid}"
    s.enabled = enabled
    s.cron_expression = cron
    s.timezone = tz
    s.created_at = created_at
    s.last_run_at = None
    if last_run_ago is not None:
        s.last_run_at = now - last_run_ago
    return s


class IsScheduleSilentlyAdvancedTests(unittest.TestCase):
    """Der gemeldete Fall: taeglich 21:00 UTC, zuletzt vor zwei Tagen gelaufen."""

    def setUp(self):
        # Ein fester "jetzt"-Zeitpunkt kurz nach dem heutigen 21:00-Slot.
        self.now = datetime(2026, 9, 7, 21, 10, tzinfo=timezone.utc)

    def test_two_missed_daily_slots_are_detected(self):
        s = _cron_schedule("s1", "0 21 * * *", last_run_ago=timedelta(days=2, minutes=10))
        self.assertTrue(is_schedule_silently_advanced(s, self.now))

    def test_a_schedule_that_ran_todays_slot_is_healthy(self):
        s = _cron_schedule("s2", "0 21 * * *", last_run_ago=timedelta(minutes=8))
        self.assertFalse(is_schedule_silently_advanced(s, self.now))

    def test_a_schedule_still_within_grace_after_firing_is_not_flagged(self):
        """last_run_at 3 Minuten nach dem Slot -- normale Dispatch-Latenz,
        kein Ausfall."""
        slot = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc)
        s = _cron_schedule("s3", "0 21 * * *")
        s.last_run_at = slot + timedelta(minutes=3)
        self.assertFalse(is_schedule_silently_advanced(s, self.now))

    def test_never_run_but_first_slot_not_yet_due_is_not_flagged(self):
        """Frisch angelegter Zeitplan (heute Morgen erstellt): die erste
        Feuerung um 21:00 steht noch bevor, das ist kein Ausfall — auch wenn
        der rein mathematische "letzte faellige Slot" (gestern 21:00) laut
        Cron-Regel schon Stunden zurueckliegt, denn den Zeitplan gab es
        gestern noch gar nicht."""
        s = _cron_schedule(
            "s4", "0 21 * * *",
            created_at=datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc),
        )  # last_run_at bleibt None
        just_before_slot = datetime(2026, 9, 7, 20, 55, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, just_before_slot))

    def test_never_run_and_first_slot_long_overdue_is_flagged(self):
        s = _cron_schedule("s5", "0 21 * * *")  # last_run_at bleibt None, created_at = lange her
        long_after = datetime(2026, 9, 7, 22, 0, tzinfo=timezone.utc)
        self.assertTrue(is_schedule_silently_advanced(s, long_after))

    def test_disabled_schedules_are_never_flagged(self):
        s = _cron_schedule("s6", "0 21 * * *", last_run_ago=timedelta(days=5), enabled=False)
        self.assertFalse(is_schedule_silently_advanced(s, self.now))

    def test_interval_only_schedules_are_out_of_scope(self):
        """Kein cron_expression -- diese Erkennung ist bewusst auf Cron
        beschraenkt (Intervall-Drift ist #718, ein anderer Defekt)."""
        s = _cron_schedule("s7", cron=None, last_run_ago=timedelta(days=5))
        s.cron_expression = None
        self.assertFalse(is_schedule_silently_advanced(s, self.now))

    def test_an_invalid_cron_expression_does_not_crash(self):
        s = _cron_schedule("s8", "not a cron expression", last_run_ago=timedelta(days=5))
        self.assertFalse(is_schedule_silently_advanced(s, self.now))

    def test_timezone_is_honoured(self):
        """21:00 Europe/Berlin ist 19:00 UTC im Sommer (CEST) -- last_run_at
        muss gegen den Slot in der RICHTIGEN Zone geprueft werden."""
        now_berlin_slot_just_fired = datetime(2026, 7, 7, 19, 5, tzinfo=timezone.utc)
        s = _cron_schedule("s9", "0 21 * * *", tz="Europe/Berlin")
        s.last_run_at = now_berlin_slot_just_fired - timedelta(minutes=2)
        self.assertFalse(is_schedule_silently_advanced(s, now_berlin_slot_just_fired))


class FindSilentlyAdvancedSchedulesTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_genuinely_advanced_one_is_returned(self):
        now = datetime(2026, 9, 7, 21, 10, tzinfo=timezone.utc)
        lost = _cron_schedule("lost", "0 21 * * *", last_run_ago=timedelta(days=2))
        healthy = _cron_schedule("healthy", "0 21 * * *", last_run_ago=timedelta(minutes=8))
        interval_only = _cron_schedule("interval", cron=None)
        interval_only.cron_expression = None

        db = _mock_db([lost, healthy, interval_only])
        found = await find_silently_advanced_schedules(db, now)

        self.assertEqual({s.id for s in found}, {"lost"})


if __name__ == "__main__":
    unittest.main()
