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

    def test_a_tick_seconds_after_the_slot_but_before_dispatch_is_not_flagged(self):
        """#803: der Waechter-Tick laeuft bewusst VOR _check_due_schedules in
        derselben 30-s-Iteration. Faellt er in das Fenster zwischen
        Faelligkeit und Dispatch, steht last_run_at noch auf GESTERN — das ist
        Dispatch-Latenz, kein verlorener Slot. Vorher schlug genau dieser Fall
        taeglich fuer jeden gesunden Zeitplan an (29 von 77 Meldungen in zwei
        Tagen), inklusive fail_count-Aufschlag und Rot-Telegram."""
        s = _cron_schedule("s3b", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 6, 21, 0, 20, tzinfo=timezone.utc)
        six_seconds_after_slot = datetime(2026, 9, 7, 21, 0, 6, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, six_seconds_after_slot))

    def test_the_grace_is_measured_from_now_not_subtracted_from_the_slot(self):
        """Gestriges last_run_at: innerhalb der Karenz nach dem Slot noch
        gesund, unmittelbar danach verloren — die Schwelle liegt auf der
        Jetzt-Seite (wie im Nie-gelaufen-Zweig), nicht am Slot."""
        s = _cron_schedule("s3c", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 6, 21, 0, 20, tzinfo=timezone.utc)
        slot = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, slot + timedelta(minutes=4, seconds=59)))
        self.assertTrue(is_schedule_silently_advanced(s, slot + timedelta(minutes=5, seconds=1)))

    def test_a_run_booked_for_the_slot_stays_healthy_long_after_the_grace(self):
        """Gegenprobe zur Karenz-Verschiebung: lief der Slot (last_run_at >=
        Slot), darf auch Stunden spaeter nichts anschlagen."""
        s = _cron_schedule("s3d", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 7, 21, 0, 20, tzinfo=timezone.utc)
        hours_later = datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, hours_later))

    def test_exactly_at_the_grace_boundary_is_still_dispatch_latency(self):
        """Randwert: genau 5 Minuten nach dem Slot gilt wie im
        Nie-gelaufen-Zweig noch als Karenz (verloren erst bei > grace)."""
        s = _cron_schedule("s3e", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 6, 21, 0, 20, tzinfo=timezone.utc)
        slot = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, slot + timedelta(minutes=5)))

    def test_a_run_stamped_exactly_on_the_slot_counts_as_run(self):
        """last_run_at == Slot (Dispatch in derselben Sekunde) ist ein Lauf,
        kein Verlust."""
        s = _cron_schedule("s3f", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, self.now))

    def test_a_late_dispatch_of_the_previous_slot_does_not_mask_the_next_loss(self):
        """10-Minuten-Takt: der :00-Slot lief (nach einem Nachholen) erst um
        :07, der :10-Slot gar nicht, jetzt ist :17. Mit der alten Formel
        (Slot minus Karenz = :05) galt :07 als "nach dem Slot" und der
        Verlust blieb unsichtbar."""
        s = _cron_schedule("s3g", "*/10 * * * *")
        s.last_run_at = datetime(2026, 9, 7, 21, 7, tzinfo=timezone.utc)
        now = datetime(2026, 9, 7, 21, 17, tzinfo=timezone.utc)
        self.assertTrue(is_schedule_silently_advanced(s, now))

    def test_a_short_period_schedule_that_is_dead_is_still_detected(self):
        """Periode <= Karenz (*/5): der juengste Slot ist IMMER in der Karenz.
        Dann zaehlt der Slot davor -- sonst waere jeder kurze Takt fuer die
        Erkennung blind (Gegenleser-Befund zu #803)."""
        s = _cron_schedule("s3h", "*/5 * * * *")
        s.last_run_at = datetime(2026, 9, 7, 18, 0, 2, tzinfo=timezone.utc)
        now = datetime(2026, 9, 7, 21, 7, tzinfo=timezone.utc)  # Slot 21:05 in Karenz, 21:00 nicht gelaufen
        self.assertTrue(is_schedule_silently_advanced(s, now))

    def test_a_short_period_schedule_that_runs_every_slot_is_healthy(self):
        s = _cron_schedule("s3i", "*/5 * * * *")
        s.last_run_at = datetime(2026, 9, 7, 21, 5, 2, tzinfo=timezone.utc)
        now = datetime(2026, 9, 7, 21, 7, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, now))

    def test_a_pending_retry_is_not_a_loss(self):
        """Transienter Skip zum Slot: _retry_or_advance legt next_run_at auf
        Slot + 12 min -- laenger als die Karenz. Der Termin ist verschoben,
        nicht verloren; erst das Aufgeben rueckt next_run_at auf den naechsten
        regulaeren Slot."""
        s = _cron_schedule("s3j", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 6, 21, 0, 20, tzinfo=timezone.utc)
        s.next_run_at = datetime(2026, 9, 7, 21, 12, tzinfo=timezone.utc)
        now = datetime(2026, 9, 7, 21, 6, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, now))

    def test_a_slot_waiting_to_be_caught_up_after_downtime_is_not_a_loss(self):
        """Scheduler stand ueber den Slot hinweg still: next_run_at zeigt noch
        auf den Slot selbst, der gleich nachgeholt wird. Das meldet
        is_schedule_missed ("wird nachgeholt") -- hier nicht nochmal, sonst
        zaehlt ein Lauf, der stattfindet, als Verlust UND als Lauf."""
        s = _cron_schedule("s3k", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 6, 21, 0, 20, tzinfo=timezone.utc)
        s.next_run_at = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc)
        now = datetime(2026, 9, 7, 21, 20, tzinfo=timezone.utc)
        self.assertFalse(is_schedule_silently_advanced(s, now))

    def test_the_reported_720_case_with_a_healthy_looking_next_run_at_is_detected(self):
        """Der Originalfall aus #720: next_run_at zeigt sauber auf morgen,
        last_run_at auf vorgestern -- der heutige Slot ist weg."""
        s = _cron_schedule("s3l", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 5, 21, 0, 20, tzinfo=timezone.utc)
        s.next_run_at = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)
        self.assertTrue(is_schedule_silently_advanced(s, self.now))

    def test_next_run_at_exactly_on_the_next_slot_means_advanced(self):
        """_calc_next_run setzt next_run_at genau auf den naechsten regulaeren
        Slot -- das ist der Zustand nach dem Aufgeben, kein anstehender Termin."""
        s = _cron_schedule("s3m", "0 21 * * *")
        s.last_run_at = datetime(2026, 9, 6, 21, 0, 20, tzinfo=timezone.utc)
        s.next_run_at = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)
        self.assertTrue(is_schedule_silently_advanced(s, self.now))

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
