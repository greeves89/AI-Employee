"""Tests for DST-aware cron scheduling (issue #206).

A cron_expression must fire at wall-clock time in the schedule's IANA
timezone, not in UTC. "0 6 * * *" with timezone="Europe/Berlin" fires at
04:00 UTC in summer (CEST, +02:00) and 05:00 UTC in winter (CET, +01:00).
next_run_at is always stored in UTC.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.scheduler_service import _calc_next_run


class _Sched:
    """Minimal duck-typed stand-in for a Schedule row."""

    def __init__(self, cron_expression=None, timezone="UTC", interval_seconds=0, next_run_at=None):
        self.cron_expression = cron_expression
        self.timezone = timezone
        self.interval_seconds = interval_seconds
        self.next_run_at = next_run_at


def test_cron_schedule_dispatches_at_expected_time():
    """06:00 Europe/Berlin in summer → 04:00 UTC (CEST is +02:00)."""
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)  # already past today's 06:00
    sched = _Sched(cron_expression="0 6 * * *", timezone="Europe/Berlin")

    nxt = _calc_next_run(sched, now)

    assert nxt.tzinfo is not None
    assert nxt.utcoffset().total_seconds() == 0  # stored in UTC
    assert nxt == datetime(2026, 7, 2, 4, 0, tzinfo=timezone.utc)


def test_utc_default_is_backwards_compatible():
    """With timezone='UTC', cron is evaluated in UTC (prior behavior)."""
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    sched = _Sched(cron_expression="0 6 * * *", timezone="UTC")

    nxt = _calc_next_run(sched, now)

    assert nxt == datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)


def test_dst_transition_handled_correctly():
    """Same cron fires 05:00 UTC in winter (CET +01:00) vs 04:00 UTC in summer."""
    sched = _Sched(cron_expression="0 6 * * *", timezone="Europe/Berlin")

    # Winter: base in January
    winter = _calc_next_run(sched, datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc))
    assert winter == datetime(2026, 1, 11, 5, 0, tzinfo=timezone.utc)

    # Summer: base in July
    summer = _calc_next_run(sched, datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc))
    assert summer == datetime(2026, 7, 11, 4, 0, tzinfo=timezone.utc)


def test_failed_run_does_not_skip_next():
    """Backend re-computes the very next slot regardless of run outcome.

    Called at 06:05 (just after a fire that may have failed), the next run
    is tomorrow 06:00 — the slot is never lost to a self-reschedule gap.
    """
    sched = _Sched(cron_expression="0 6 * * *", timezone="Europe/Berlin")
    # 06:05 Berlin summer == 04:05 UTC
    now = datetime(2026, 7, 2, 4, 5, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    assert nxt == datetime(2026, 7, 3, 4, 0, tzinfo=timezone.utc)


def test_invalid_timezone_falls_back_to_utc():
    """An unknown tz must not crash the scheduler — evaluate cron in UTC."""
    sched = _Sched(cron_expression="0 6 * * *", timezone="Mars/Olympus")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    assert nxt == datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)


def test_missing_timezone_attr_defaults_utc():
    """Legacy rows without a timezone attribute default to UTC."""
    class _Legacy:
        cron_expression = "0 6 * * *"
        interval_seconds = 0

    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    nxt = _calc_next_run(_Legacy(), now)

    assert nxt == datetime(2026, 7, 2, 6, 0, tzinfo=timezone.utc)


def test_interval_schedule_unaffected():
    """Interval schedules ignore timezone entirely.

    Uses an ALREADY-anchored schedule (next_run_at set) so the assertion is
    about timezone-independence specifically — a brand-new, still-unanchored
    hourly-or-longer schedule instead gets its first phase deliberately
    placed away from :00/:30 (#718 Punkt 2, see the dedicated tests below),
    which would make a bare `now + interval` assertion here incidental to
    that unrelated behavior rather than to the timezone claim this test makes.
    """
    anchor = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)
    sched = _Sched(cron_expression=None, timezone="Europe/Berlin", interval_seconds=3600,
                    next_run_at=anchor)
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    assert nxt == datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)


# Issue #718: `now + interval` carried every dispatch delay, retry wait and
# lock forward PERMANENTLY (it can only drift forward, never back). Over six
# days a 2h-interval schedule drifted from :55 to :58 past the hour — close
# enough to every full-hour cron tick that it made the agent "busy" at EVERY
# such tick, starving out cron schedules that share the agent. Anchoring to
# the schedule's own `next_run_at` (the slot that just fired) instead of
# `now` keeps the phase fixed regardless of how late any individual dispatch
# runs.

def test_the_phase_is_anchored_to_the_original_slot_not_to_now():
    """A slot due at :55 that actually fires late (at :58, e.g. a slow tick)
    must still produce the NEXT slot at :55 — not :58."""
    anchor = datetime(2026, 9, 1, 10, 55, tzinfo=timezone.utc)  # the slot that just fired
    fired_late_at = datetime(2026, 9, 1, 10, 58, tzinfo=timezone.utc)  # 3 min dispatch delay
    sched = _Sched(interval_seconds=7200, next_run_at=anchor)

    nxt = _calc_next_run(sched, fired_late_at)

    assert nxt == datetime(2026, 9, 1, 12, 55, tzinfo=timezone.utc)  # still :55, not :58


def test_the_phase_never_drifts_across_many_delayed_cycles():
    """Simulates six days of a 2h-interval schedule, each fire delayed by a
    growing amount (the reported real pattern: :55 -> :58 over six days with
    the OLD now-anchored formula). With the fix the phase must stay put."""
    anchor = datetime(2026, 9, 1, 0, 55, tzinfo=timezone.utc)
    next_run_at = anchor
    for cycle in range(6 * 12):  # 6 days of 2h cycles
        delay = timedelta(seconds=30 + cycle)  # a growing, but always-present delay
        fired_at = next_run_at + delay
        next_run_at = _calc_next_run(_Sched(interval_seconds=7200, next_run_at=next_run_at), fired_at)
    assert next_run_at.minute == 55  # phase held, despite ~200 delayed dispatches


def test_a_schedule_without_a_next_run_at_attribute_falls_back_to_now():
    """Creating a NEW interval schedule calls _calc_next_run on the request
    body (ScheduleCreate), which has no `next_run_at` field at all — must not
    raise AttributeError.

    interval_seconds is deliberately BELOW the #718-Punkt-2 threshold (see
    below) so this stays about the attribute-safety net, not about the
    flexible-phase placement of long background schedules."""
    class _BareInterval:
        cron_expression = None
        interval_seconds = 1800
        # deliberately no next_run_at attribute

    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    nxt = _calc_next_run(_BareInterval(), now)

    assert nxt == datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)


def test_a_schedule_with_next_run_at_none_also_falls_back_to_now():
    sched = _Sched(interval_seconds=1800, next_run_at=None)
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    assert nxt == datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)


def test_a_naive_anchor_is_treated_as_utc():
    """SQLite drops tzinfo on round-trip; Postgres never does."""
    anchor = datetime(2026, 9, 1, 10, 55)  # naive
    now = datetime(2026, 9, 1, 10, 58, tzinfo=timezone.utc)
    sched = _Sched(interval_seconds=7200, next_run_at=anchor)

    nxt = _calc_next_run(sched, now)

    assert nxt == datetime(2026, 9, 1, 12, 55, tzinfo=timezone.utc)


# Issue #718 Punkt 2: the very FIRST anchor of a new, unbefristeten (no cron)
# interval schedule used to land on whatever second `now` happened to be —
# and the Punkt-1 anchor-fix above then holds that phase fixed FOREVER. The
# real incident anchored a 2h background schedule at :55 and it drifted to
# :58, starving out every hourly cron tick sharing the agent. Placing the
# first phase deliberately at :20 keeps long background schedules away from
# the usual :00/:30 cron ticks from the very start.

def test_a_new_long_interval_schedule_anchors_away_from_the_full_hour():
    sched = _Sched(cron_expression=None, interval_seconds=7200, next_run_at=None)
    now = datetime(2026, 9, 1, 10, 47, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    # now + 7200s = 12:47 -> naechste :20 danach ist 12:20? Nein, 12:20 < 12:47,
    # also die Stunde danach: 13:20.
    assert nxt == datetime(2026, 9, 1, 13, 20, tzinfo=timezone.utc)


def test_the_flexible_phase_is_still_at_least_one_full_interval_ahead():
    """The snap must never fire the FIRST run sooner than a plain now+interval
    would — only reposition it within roughly the same window."""
    sched = _Sched(cron_expression=None, interval_seconds=3600, next_run_at=None)
    now = datetime(2026, 9, 1, 10, 5, tzinfo=timezone.utc)  # now+1h = 11:05

    nxt = _calc_next_run(sched, now)

    assert nxt >= now + timedelta(seconds=3600)
    assert nxt.minute == 20


def test_a_cron_schedule_is_never_snapped_even_when_anchor_is_missing():
    """The flexible-phase snap is only for pure interval schedules — a cron
    schedule that (via a broken expression) falls through to the interval
    branch must keep the plain now+step fallback."""
    sched = _Sched(cron_expression="not a valid cron", interval_seconds=7200, next_run_at=None)
    now = datetime(2026, 9, 1, 10, 47, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    assert nxt == now + timedelta(seconds=7200)


def test_short_interval_schedules_are_not_snapped():
    """Below the #718 threshold, a schedule fires often enough that a fixed
    target minute would only delay the first run without helping anything."""
    sched = _Sched(cron_expression=None, interval_seconds=1800, next_run_at=None)
    now = datetime(2026, 9, 1, 10, 47, tzinfo=timezone.utc)

    nxt = _calc_next_run(sched, now)

    assert nxt == now + timedelta(seconds=1800)
