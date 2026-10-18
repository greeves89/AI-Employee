"""Issue #718, Punkt 3: "Verschieben genauso sichtbar machen wie Verwerfen".

The #720 fix already alerts the operator via Telegram when a cron schedule
silently dropped a due slot (`find_silently_advanced_schedules`) — but the
schedule's OWN health metrics (`total_runs`/`fail_count`, and therefore
`success_rate`) stayed untouched, so a two-day-dead daily report still read
as a perfect track record to anyone checking the schedule itself instead of
watching Telegram. This test covers the missing half: the same detection
must also book the loss into the schedule's own counters, exactly once per
newly-detected loss (not on every tick).

#1001: the dedup marker alone (just `last_run_at`) made a schedule that never
runs again report EXACTLY ONCE, forever, no matter how long it stays dead —
the heaviest outage became the quietest. The marker now carries the missed
slot COUNT too, and re-books/re-alerts on every doubling of that count; these
tests therefore freeze `now` (via `app.services.scheduler_service.datetime`)
so the number of missed daily 21:00 slots since `last_run_at` is a fixed,
test-controlled value instead of drifting with wall-clock time.
"""
import json
from datetime import datetime, timedelta, timezone
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
        redis.client.get = AsyncMock(return_value=None)  # echtes Redis: fehlender Key -> None
        svc.redis = redis
    else:
        svc.redis = None
    return svc


class _FakeSchedule:
    def __init__(self, id="s1", name="Tagesabschluss-Bericht",
                 last_run_at=datetime(2026, 9, 5, 21, 0, 2, tzinfo=timezone.utc),
                 total_runs=27, fail_count=0,
                 cron_expression="0 21 * * *", enabled=True):
        self.id = id
        self.name = name
        self.last_run_at = last_run_at
        self.total_runs = total_runs
        self.fail_count = fail_count
        self.cron_expression = cron_expression
        self.enabled = enabled


class _FakeSession:
    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patched(svc, fortgeschritten, session, now=None):
    patches = [
        patch("app.services.scheduler_service.resilient_session", return_value=session),
        patch("app.services.scheduler_service.find_missed_schedules", AsyncMock(return_value=[])),
        patch("app.services.scheduler_service.find_silently_advanced_schedules",
              AsyncMock(return_value=fortgeschritten)),
    ]
    if now is not None:
        dt_patch = patch("app.services.scheduler_service.datetime")
        patches.append(dt_patch)
        return patches, now
    return patches, None


class _frozen:
    """Freezes `datetime.now()` inside scheduler_service to exactly `now`,
    one daily 21:00-slot's worth of loss past the schedule's `last_run_at` by
    construction of the test fixture's cron rule — so the missed-slot COUNT
    (not just the yes/no detection) is deterministic across test runs."""

    def __init__(self, now):
        self._now = now
        self._patch = patch("app.services.scheduler_service.datetime")

    def __enter__(self):
        dt = self._patch.__enter__()
        dt.now.return_value = self._now
        dt.fromtimestamp = datetime.fromtimestamp
        return dt

    def __exit__(self, *exc):
        return self._patch.__exit__(*exc)


# Genau EIN faelliger 21:00-Slot zwischen last_run_at (2026-09-05 21:00:02)
# und NOW: der naechste Slot (2026-09-06 21:00:00) ist faellig und bereits
# ausserhalb der 5-Minuten-Dispatch-Karenz, der Slot danach (2026-09-07) noch
# nicht erreicht.
NOW_ONE_LOSS = datetime(2026, 9, 6, 21, 10, 0, tzinfo=timezone.utc)
# Nach einem echten Lauf am 2026-09-08 21:00:02 wieder genau EIN neuer
# Verlust bis zu diesem Zeitpunkt.
NOW_SECOND_LOSS = datetime(2026, 9, 9, 21, 10, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_silently_advanced_schedule_gets_its_counters_bumped():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    assert schedule.total_runs == 28
    assert schedule.fail_count == 1
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_the_same_last_run_at_is_not_double_counted_on_the_next_tick():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()
        await svc._tick_missed_schedule_watchdog()

    # Zwei Ticks, derselbe last_run_at UND derselbe Verlust-Bucket -> nur EIN
    # Aufschlag, nicht zwei.
    assert schedule.total_runs == 28
    assert schedule.fail_count == 1


@pytest.mark.asyncio
async def test_a_genuine_new_run_resets_the_dedup_and_a_fresh_loss_counts_again():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1

    # Der Zeitplan lief inzwischen wieder (last_run_at aktualisiert), verliert
    # danach aber einen NEUEN Slot -- das muss erneut zaehlen.
    schedule.last_run_at = datetime(2026, 9, 8, 21, 0, 2, tzinfo=timezone.utc)
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_SECOND_LOSS):
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 2
    assert schedule.total_runs == 29


@pytest.mark.asyncio
async def test_a_healthy_schedule_is_left_alone():
    svc = _make_service()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_alert_still_fires_alongside_the_counter_bump():
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    svc.redis.client.publish.assert_awaited_once()
    channel, raw = svc.redis.client.publish.await_args.args
    assert channel == "telegram:notification"
    payload = json.loads(raw)
    assert "lautlos" in payload["text"]


@pytest.mark.asyncio
async def test_the_alert_names_the_number_of_lost_slots():
    """#1001: die Meldung muss die Schwere nennen, nicht nur dass verloren
    wurde -- sonst sieht ein 19-Tage-Ausfall im Text wie ein Blip aus."""
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    channel, raw = svc.redis.client.publish.await_args.args
    payload = json.loads(raw)
    assert "1" in payload["text"]


@pytest.mark.asyncio
async def test_a_schedule_dead_for_many_slots_re_alerts_on_each_doubling():
    """Der Kernfall aus #1001: last_run_at bleibt fuer immer gleich (der
    Zeitplan laeuft nie wieder), aber die Zahl der verlorenen Slots waechst
    mit jedem Tick weiter. Die alte Marke (nur last_run_at) haette nach dem
    ERSTEN Tick fuer immer geschwiegen; die neue muss bei 1, 2, 4, 8 erneut
    melden -- und bei 3, 5, 6, 7 NICHT (sonst waere es wieder "jede
    Erkennung", nur mit mehr Rauschen)."""
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    alerts_after = []
    for missed_slots in range(1, 9):
        now = datetime(2026, 9, 5, 21, 0, 2, tzinfo=timezone.utc) + timedelta(
            days=missed_slots, minutes=10
        )
        p1, p2, p3 = _patched(svc, [schedule], session)[0]
        with p1, p2, p3, _frozen(now):
            await svc._tick_missed_schedule_watchdog()
        alerts_after.append(svc.redis.client.publish.await_count)

    # Meldungen bei genau 1, 2, 4, 8 (Index 0, 1, 3, 7) -- je einmal neu,
    # dazwischen (2, 4, 5, 6 -> Index 2, 4, 5, 6) keine weitere.
    assert alerts_after == [1, 2, 2, 3, 3, 3, 3, 4]
    assert schedule.fail_count == 8
    assert schedule.total_runs == 35


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

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    svc.redis.client.set.assert_awaited_once()
    key, value = svc.redis.client.set.await_args.args[:2]
    assert key == _marker_key("s1")
    # Marke jetzt "<last_run_at>|<verlorene Slots>" statt nur der Marke (#1001).
    assert value == f"{schedule.last_run_at.isoformat()}|1"


@pytest.mark.asyncio
async def test_a_restarted_service_does_not_book_the_same_loss_again():
    """Frischer Prozess (leeres Dict), aber Redis kennt die Marke schon:
    kein zweiter Aufschlag, kein zweites Telegram."""
    schedule = _FakeSchedule()
    session = _FakeSession()

    svc_before_restart = _make_service()
    p1, p2, p3 = _patched(svc_before_restart, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc_before_restart._tick_missed_schedule_watchdog()
    assert schedule.fail_count == 1
    _, persisted = svc_before_restart.redis.client.set.await_args.args[:2]

    svc_after_restart = _make_service()  # _silently_advanced_alerted == {}
    svc_after_restart.redis.client.get = AsyncMock(return_value=persisted)
    p1, p2, p3 = _patched(svc_after_restart, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc_after_restart._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    assert schedule.total_runs == 28
    svc_after_restart.redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_legacy_marker_without_a_count_is_recognised():
    """Vor #1001 persistierte Marken trugen nur `last_run_at`, keinen
    Zaehler. Nach dem Rollout muss das nicht sofort jeden schon gemeldeten
    Fall erneut auslösen -- die alte Form gilt als Bucket 0 (bereits
    gebucht)."""
    schedule = _FakeSchedule()
    session = _FakeSession()
    svc = _make_service()
    svc.redis.client.get = AsyncMock(
        return_value=schedule.last_run_at.isoformat()  # alte Form, kein "|N"
    )
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 0
    svc.redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bytes_marker_from_redis_is_recognised():
    """redis-py liefert ohne decode_responses Bytes — die Marke muss trotzdem
    als bekannt gelten."""
    schedule = _FakeSchedule()
    session = _FakeSession()
    svc = _make_service()
    svc.redis.client.get = AsyncMock(
        return_value=f"{schedule.last_run_at.isoformat()}|1".encode()
    )
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
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
    old_marker = f"{datetime(2026, 9, 4, 21, 0, 2, tzinfo=timezone.utc).isoformat()}|1"
    svc.redis.client.get = AsyncMock(return_value=old_marker)
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
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

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
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

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
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

    p1, p2, p3 = _patched(svc, [a, b], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
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
    schedule.interval_seconds = 0
    session = _FakeSession()

    await svc._report_dropped_slot(
        schedule, datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc),
        reason="agent_busy", attempts=3,
    )
    assert schedule.fail_count == 1 and schedule.total_runs == 28

    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(NOW_ONE_LOSS):
        await svc._tick_missed_schedule_watchdog()

    assert schedule.fail_count == 1
    assert schedule.total_runs == 28
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_dropped_slot_does_not_reset_an_already_higher_missed_count():
    """Gegenlesen-Fund zu #1001: der Tick-Waechter kann fuer denselben
    `last_run_at` bereits einen hohen `missed_count` gebucht haben (hier: 6,
    ueber mehrere Ticks eskaliert). Meldet `_report_dropped_slot` DANACH
    einen einzelnen, in Echtzeit aufgegebenen Slot fuer DENSELBEN Marker, darf
    das den gebuchten Stand nicht auf 1 zuruecksetzen -- sonst saehe der
    naechste Tick ein Delta von 6-1=5 statt der tatsaechlich einen neuen
    verlorenen Slot und buchte `fail_count` um ein Vielfaches zu hoch."""
    svc = _make_service()
    schedule = _FakeSchedule()
    schedule.interval_seconds = 0
    session = _FakeSession()

    # Tick-Waechter eskaliert zuerst auf 6 verlorene Slots (Bucket 2).
    now_six_losses = datetime(2026, 9, 5, 21, 0, 2, tzinfo=timezone.utc) + timedelta(
        days=6, minutes=10
    )
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(now_six_losses):
        await svc._tick_missed_schedule_watchdog()
    assert schedule.fail_count == 6

    # _report_dropped_slot meldet DANACH einen einzelnen Slot in Echtzeit --
    # last_run_at (und damit der Marker) bleibt dabei unveraendert.
    await svc._report_dropped_slot(
        schedule, datetime(2026, 9, 13, 21, 0, tzinfo=timezone.utc),
        reason="agent_busy", attempts=3,
    )

    # Naechster Tick, ein Slot weiter (7 insgesamt, noch Bucket 2) -- darf
    # NICHT so buchen, als waeren erst 1 oder 2 Slots bekannt gewesen.
    now_seven_losses = now_six_losses + timedelta(days=1)
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(now_seven_losses):
        await svc._tick_missed_schedule_watchdog()

    # fail_count: 6 (Tick) + 1 (Echtzeit-Slot) = 7. Der naechste Tick sieht
    # croninatisch 7 faellige Slots, bucket(7)==bucket(bekannt=7) -> keine
    # weitere Buchung (der 7. Slot ist exakt der, den der Echtzeit-Pfad schon
    # gezaehlt hat) -- NICHT 6 + 1 + 5 (faelschlich zurueckgesetzt auf 1 und
    # dann um 6 nachgeholt).
    assert schedule.fail_count == 7
    assert schedule.total_runs == schedule.fail_count + 27


@pytest.mark.asyncio
async def test_repeated_realtime_dropouts_are_not_double_counted_by_the_tick():
    """Gegenlesen-Fund zu #1001 (zweite Runde): ein taeglicher Zeitplan, dessen
    Lauf jeden Tag per Retry aufgegeben wird (last_run_at bewegt sich NIE,
    weil kein Versuch je durchlief), rief VOR diesem Fix bei jedem Give-up
    `_remember_silently_advanced(..., missed_count=1)` -- ein fester Wert, der
    den bekannten Stand nie ueber 1 hinaus anhob. Zwei echte Give-ups fuer
    last_run_at zaehlten `fail_count` real korrekt auf 2 hoch, aber der
    Tick-Waechter hielt den bekannten Stand bei 1 und bucht beim naechsten
    Tick dieselben bereits gezaehlten Slots ein zweites Mal (2 echte + hier
    faelschlich nochmal welche). Mit dem laufenden Zaehler (statt fest `1`)
    bleibt `fail_count` exakt bei der Zahl der tatsaechlich verlorenen Slots."""
    svc = _make_service()
    schedule = _FakeSchedule()
    session = _FakeSession()

    # Zwei echte Give-ups fuer T1 (06.09.) und T2 (07.09.) -- last_run_at
    # (und damit der Marker) bleibt dabei unveraendert, da kein Lauf durchkam.
    await svc._report_dropped_slot(
        schedule, datetime(2026, 9, 6, 21, 0, tzinfo=timezone.utc),
        reason="overload", attempts=2,
    )
    await svc._report_dropped_slot(
        schedule, datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc),
        reason="overload", attempts=2,
    )
    assert schedule.fail_count == 2  # zwei echte Verluste, direkt gezaehlt

    # Der Tick-Waechter sieht per Cron-Mathematik jetzt 3 faellige Slots
    # (06., 07., 08.09.) seit demselben last_run_at -- NUR der dritte (08.09.)
    # ist dem Echtzeit-Pfad noch unbekannt.
    now_three_losses = datetime(2026, 9, 8, 21, 10, 0, tzinfo=timezone.utc)
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(now_three_losses):
        await svc._tick_missed_schedule_watchdog()

    # NICHT 4 (2 echte + faelschlich nochmal 2 ueber den Tick, weil der
    # bekannte Stand vor dem Fix bei 1 statt 2 eingefroren war).
    assert schedule.fail_count == 2
    assert schedule.total_runs == schedule.fail_count + 27

    # Der dritte Slot (08.09.) holt beim naechsten Bucket-Sprung nach, sobald
    # ein vierter faelliger Slot den Bucket von 2 auf 4 anhebt -- die
    # Gesamtsumme bleibt exakt, nur die Meldung kommt gebuendelt statt pro Tag.
    now_four_losses = now_three_losses + timedelta(days=1)
    p1, p2, p3 = _patched(svc, [schedule], session)[0]
    with p1, p2, p3, _frozen(now_four_losses):
        await svc._tick_missed_schedule_watchdog()
    assert schedule.fail_count == 4
    assert schedule.total_runs == schedule.fail_count + 27
