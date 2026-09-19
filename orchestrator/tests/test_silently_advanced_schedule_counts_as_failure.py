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


# --- #803 Klasse B: die Dedup-Marke muss einen Neustart ueberleben ---------
#
# _silently_advanced_alerted lebte nur im Prozessspeicher. Jeder Neustart
# (bei uns: jeder Merge = Deploy) meldete alle Zeitplaene mit zurueckliegendem
# last_run_at ERNEUT und buchte den Verlust ein zweites Mal (48 von 77
# Meldungen in zwei Tagen). Redis traegt die Marke ueber den Neustart; ohne
# Redis bleibt der Speicher-Fallback, damit die Erkennung selbst (wie in #720
# gefordert) weiter ohne Redis funktioniert.


def _marker_key(schedule_id: str) -> str:
    from app.services.scheduler_service import _silently_advanced_marker_key
    return _silently_advanced_marker_key(schedule_id)


@pytest.mark.asyncio
async def test_the_marker_is_persisted_to_redis_when_a_loss_is_booked():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    svc.redis.client.set.assert_awaited_once()
    key, value = svc.redis.client.set.await_args.args[:2]
    assert key == _marker_key("s1")
    assert value == schedule.last_run_at.isoformat()


@pytest.mark.asyncio
async def test_a_restarted_service_does_not_book_the_same_loss_again():
    """Frischer Prozess (leeres Dict), aber Redis kennt die Marke schon:
    kein zweiter Aufschlag, kein zweites Telegram."""
    schedule = _FakeSchedule()
    session = _FakeSession()

    svc_before_restart = _make_service()
    p1, p2, p3 = _patched(svc_before_restart, [schedule], session)
    with p1, p2, p3:
        await svc_before_restart._tick_missed_schedule_watchdog()
    assert schedule.fail_count == 1
    _, persisted = svc_before_restart.redis.client.set.await_args.args[:2]

    svc_after_restart = _make_service()  # _silently_advanced_alerted == {}
    svc_after_restart.redis.client.get = AsyncMock(return_value=persisted)
    p1, p2, p3 = _patched(svc_after_restart, [schedule], session)
    with p1, p2, p3:
        await svc_after_restart._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    assert schedule.total_runs == 28
    svc_after_restart.redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bytes_marker_from_redis_is_recognised():
    """redis-py liefert ohne decode_responses Bytes — die Marke muss trotzdem
    als bekannt gelten."""
    schedule = _FakeSchedule()
    session = _FakeSession()
    svc = _make_service()
    svc.redis.client.get = AsyncMock(
        return_value=schedule.last_run_at.isoformat().encode()
    )
    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 0
    svc.redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_new_loss_after_a_real_run_is_booked_despite_an_old_redis_marker():
    """Redis kennt die Marke fuer den ALTEN last_run_at; inzwischen lief der
    Zeitplan wieder und verlor einen NEUEN Slot — das muss zaehlen."""
    schedule = _FakeSchedule()
    session = _FakeSession()
    svc = _make_service()
    old_marker = datetime(2026, 9, 4, 21, 0, 2, tzinfo=timezone.utc).isoformat()
    svc.redis.client.get = AsyncMock(return_value=old_marker)
    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    svc.redis.client.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_without_redis_the_in_memory_fallback_still_books_exactly_once():
    """#720-Anforderung bleibt: die Erkennung braucht kein Redis. Ohne Redis
    zaehlt der Verlust genau einmal je Prozess (Speicher-Fallback)."""
    svc = _make_service(with_redis=False)
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    assert schedule.total_runs == 28


@pytest.mark.asyncio
async def test_a_failing_redis_lookup_falls_back_to_memory_and_still_alerts():
    svc = _make_service()
    svc.redis.client.get = AsyncMock(side_effect=ConnectionError("redis weg"))
    svc.redis.client.set = AsyncMock(side_effect=ConnectionError("redis weg"))
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    svc.redis.client.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_redis_key_is_per_schedule_and_the_ttl_is_set():
    """Gegenleser-Befund: ein Schluessel ohne Zeitplan-ID liesse alle
    Zeitplaene eine Marke teilen -- der zweite Verlust des Tages waere
    unsichtbar. Und ohne TTL blieben Marken geloeschter Zeitplaene ewig."""
    svc = _make_service()
    a = _FakeSchedule(id="a1", name="A")
    b = _FakeSchedule(id="b2", name="B")
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [a, b], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    calls = svc.redis.client.set.await_args_list
    keys = [c.args[0] for c in calls]
    assert keys == [
        "scheduler:silently_advanced_alerted:a1",
        "scheduler:silently_advanced_alerted:b2",
    ]
    assert all(c.kwargs.get("ex") == 30 * 24 * 3600 for c in calls)
    assert a.fail_count == 1 and b.fail_count == 1


@pytest.mark.asyncio
async def test_a_loss_already_booked_by_report_dropped_slot_is_not_booked_twice():
    """_report_dropped_slot (#631) zaehlt einen endgueltig verworfenen Slot
    selbst und laesst last_run_at stehen -- danach sieht der Waechter genau
    das Bild eines lautlosen Verlusts. Ohne gemeinsame Marke zaehlte jeder
    ordentlich gemeldete Verlust doppelt."""
    svc = _make_service()
    schedule = _FakeSchedule()
    schedule.cron_expression = "0 21 * * *"
    schedule.interval_seconds = 0
    session = _FakeSession()

    await svc._report_dropped_slot(
        schedule, datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc),
        reason="agent_busy", attempts=3,
    )
    assert schedule.fail_count == 1 and schedule.total_runs == 28

    p1, p2, p3 = _patched(svc, [schedule], session)
    with p1, p2, p3:
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    assert schedule.total_runs == 28
    session.commit.assert_not_awaited()
