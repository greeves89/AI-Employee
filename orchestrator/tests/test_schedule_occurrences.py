"""schedule_occurrences(): alle Termine eines Zeitplans in einem Zeitraum —
Grundlage fuer die Termin-Marken auf der Agenten-Tagesleiste (HANDOVER.md
Schritt 2). Rein rechnerisch aus cron_expression/interval_seconds, unabhaengig
davon ob der Lauf tatsaechlich stattfand (das zeigen die Task-Balken).
"""

import unittest
from datetime import datetime, timedelta, timezone

from app.services.scheduler_service import schedule_occurrences


class _FakeSchedule:
    def __init__(self, cron_expression=None, interval_seconds=0, timezone_="UTC", next_run_at=None):
        self.cron_expression = cron_expression
        self.interval_seconds = interval_seconds
        self.timezone = timezone_
        self.next_run_at = next_run_at


class CronOccurrencesTests(unittest.TestCase):
    def test_daily_cron_within_a_week(self):
        s = _FakeSchedule(cron_expression="0 6 * * *")
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 8, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 7)
        self.assertTrue(all(t.hour == 6 and t.minute == 0 for t in got))
        self.assertEqual(got, sorted(got))

    def test_occurrence_exactly_at_range_start_is_included(self):
        s = _FakeSchedule(cron_expression="0 6 * * *")
        start = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertIn(start, got)

    def test_range_end_is_exclusive(self):
        s = _FakeSchedule(cron_expression="0 6 * * *")
        end = datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc)
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertNotIn(end, got)

    def test_past_range_works_too(self):
        """Die Vergangenheit muss genauso berechenbar sein wie die Zukunft —
        Kernanforderung: 'Ich will die Tage auch in Zukunft sehen und die
        vergangenen Tage auch!'"""
        s = _FakeSchedule(cron_expression="0 9 * * *")
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2020, 1, 4, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 3)

    def test_timezone_aware_cron(self):
        """0 6 * * * in Europe/Berlin ist 04:00 UTC im Sommer (CEST)."""
        s = _FakeSchedule(cron_expression="0 6 * * *", timezone_="Europe/Berlin")
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].hour, 4)

    def test_invalid_cron_returns_empty_not_raises(self):
        s = _FakeSchedule(cron_expression="not a cron")
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        self.assertEqual(schedule_occurrences(s, start, end), [])

    def test_unknown_timezone_falls_back_to_utc_instead_of_raising(self):
        s = _FakeSchedule(cron_expression="0 6 * * *", timezone_="Not/AZone")
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].hour, 6)


class IntervalOccurrencesTests(unittest.TestCase):
    def test_hourly_interval_within_a_day(self):
        anchor = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        s = _FakeSchedule(interval_seconds=3600, next_run_at=anchor)
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 24)

    def test_range_far_before_the_anchor(self):
        """Ein Zeitplan, der erst kuerzlich angelegt wurde: next_run_at liegt
        weit in der Zukunft relativ zum angefragten (vergangenen) Zeitraum —
        die Ganzzahl-Arithmetik muss trotzdem korrekt zurueckrechnen."""
        anchor = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
        s = _FakeSchedule(interval_seconds=3600 * 6, next_run_at=anchor)
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 4)
        for t in got:
            self.assertEqual((t - anchor).total_seconds() % (3600 * 6), 0)

    def test_range_far_after_the_anchor(self):
        anchor = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        s = _FakeSchedule(interval_seconds=3600 * 12, next_run_at=anchor)
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertEqual(len(got), 2)

    def test_no_next_run_at_returns_empty(self):
        s = _FakeSchedule(interval_seconds=3600, next_run_at=None)
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        self.assertEqual(schedule_occurrences(s, start, end), [])

    def test_zero_interval_and_no_cron_returns_empty(self):
        s = _FakeSchedule(interval_seconds=0, next_run_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        self.assertEqual(schedule_occurrences(s, start, end), [])


class RangeSanityTests(unittest.TestCase):
    def test_empty_or_inverted_range_returns_empty(self):
        s = _FakeSchedule(cron_expression="0 6 * * *")
        t = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.assertEqual(schedule_occurrences(s, t, t), [])
        self.assertEqual(schedule_occurrences(s, t, t - timedelta(hours=1)), [])

    def test_never_exceeds_the_hard_cap(self):
        """Ein bösartiger oder kaputter Ausdruck (jede Minute, riesiger Bereich)
        darf den Server nicht in eine Endlosschleife oder OOM schicken."""
        s = _FakeSchedule(cron_expression="* * * * *")
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        got = schedule_occurrences(s, start, end)
        self.assertLessEqual(len(got), 1000)


if __name__ == "__main__":
    unittest.main()
