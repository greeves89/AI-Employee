"""Issue #718, Punkt 3: "Verschieben genauso sichtbar machen wie Verwerfen".

The #720 fix already alerts the operator via Telegram when a cron schedule
silently dropped a due slot (`find_silently_advanced_schedules`) — but the
schedule's OWN health metrics (`total_runs`/`fail_count`, and therefore
`success_rate`) stayed untouched, so a two-day-dead daily report still read
as a perfect track record to anyone checking the schedule itself instead of
watching Telegram. This test covers the missing half: the same detection
must also book the loss into the schedule's own counters, exactly once per
newly-detected `last_run_at` (not on every tick).
"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scheduler_service import SchedulerService


def _make_service(with_redis: bool = True) -> SchedulerService:
    svc = SchedulerService.__new__(SchedulerService)
    svc._missed_alerted = {}
    svc._silently_advanced_alerted = {}
    if with_redis:
        redis = MagicMock()
        redis.client = AsyncMock()
        redis.client.publish = AsyncMock()
        svc.redis = redis
    else:
        svc.redis = None
    return svc


class _FakeSchedule:
    def __init__(self, id="s1", name="Tagesabschluss-Bericht",
                 last_run_at=datetime(2026, 9, 5, 21, 0, 2, tzinfo=timezone.utc),
                 total_runs=27, fail_count=0):
        self.id = id
        self.name = name
        self.last_run_at = last_run_at
        self.total_runs = total_runs
        self.fail_count = fail_count


class _FakeSession:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patched(svc, fortgeschritten, session):
    return (
        patch("app.services.scheduler_service.resilient_session", return_value=session),
        patch("app.services.scheduler_service.find_missed_schedules", AsyncMock(return_value=[])),
        patch("app.services.scheduler_service.find_silently_advanced_schedules",
              AsyncMock(return_value=fortgeschritten)),
    )


@pytest.mark.asyncio
async def test_a_silently_advanced_schedule_gets_its_counters_bumped():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    assert schedule.total_runs == 28
    assert schedule.fail_count == 1
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_the_same_last_run_at_is_not_double_counted_on_the_next_tick():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()
        await svc._tick_missed_schedule_watchdog()

    # Zwei Ticks, derselbe last_run_at -> nur EIN Aufschlag, nicht zwei.
    assert schedule.total_runs == 28
    assert schedule.fail_count == 1


@pytest.mark.asyncio
async def test_a_genuine_new_run_resets_the_dedup_and_a_fresh_loss_counts_again():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1

    # Der Zeitplan lief inzwischen wieder (last_run_at aktualisiert), verliert
    # danach aber einen NEUEN Slot -- das muss erneut zaehlen.
    schedule.last_run_at = datetime(2026, 9, 8, 21, 0, 2, tzinfo=timezone.utc)
    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 2
    assert schedule.total_runs == 29


@pytest.mark.asyncio
async def test_a_healthy_schedule_is_left_alone():
    svc = _make_service()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_alert_still_fires_alongside_the_counter_bump():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    svc.redis.client.publish.assert_awaited_once()
    channel, raw = svc.redis.client.publish.await_args.args
    assert channel == "telegram:notification"
    payload = json.loads(raw)
    assert "lautlos" in payload["text"]
