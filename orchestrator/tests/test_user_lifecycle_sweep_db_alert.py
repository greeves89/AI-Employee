"""Issue #617: background jobs (scheduler, OAuth-sweep, user-lifecycle,
disk-monitor) couldn't reach the DB for hours, with "no circuit-breaker/
alerting/self-heal" for the affected subsystems. SchedulerService already got
this treatment for its DueSchedules tick (#601/#719,
test_scheduler_due_schedules_db_alert.py) — UserLifecycleService._sweep() had
none: a DB outage just produced a plain ERROR log line every 60s
("[UserLifecycle] Sweep error") with nobody told, for as long as the outage
lasted.

This mirrors the scheduler's escalating-alert pattern one-to-one: a single
failed tick self-heals silently (still just a WARNING), but
_SWEEP_ALERT_THRESHOLD consecutive failures — and every doubling after that —
write an urgent Notification and publish a Telegram alert via
_alert_sweep_down().
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.user_lifecycle import (
    _SWEEP_ALERT_THRESHOLD,
    _TRANSIENT_DB_ERRORS,
    UserLifecycleService,
)


def _make_service(with_redis: bool = True) -> UserLifecycleService:
    svc = UserLifecycleService.__new__(UserLifecycleService)
    svc.db_factory = MagicMock()
    svc.docker = MagicMock()
    svc._running = False
    svc._sweep_fail_streak = 0
    svc._sweep_first_fail_at = None
    svc._sweep_next_alert_streak = _SWEEP_ALERT_THRESHOLD
    if with_redis:
        redis = MagicMock()
        redis.client = AsyncMock()
        redis.client.publish = AsyncMock()
        svc.redis = redis
    else:
        svc.redis = None
    return svc


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_alert_publishes_telegram_and_writes_notification():
    svc = _make_service()
    session = _FakeSession()

    with patch(
        "app.services.user_lifecycle.resilient_session",
        return_value=session,
    ):
        await svc._alert_sweep_down(_SWEEP_ALERT_THRESHOLD)

    svc.redis.client.publish.assert_awaited_once()
    channel, raw = svc.redis.client.publish.await_args.args
    assert channel == "telegram:notification"
    payload = json.loads(raw)
    assert "nicht erreichbar" in payload["text"]

    assert len(session.added) == 1
    notification = session.added[0]
    assert notification.priority == "urgent"
    assert notification.type == "error"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_alert_still_publishes_telegram_when_db_still_down():
    """Die Notification laesst sich nicht schreiben (DB weiter unten), der
    Telegram-Weg laeuft aber ueber Redis, nicht ueber die DB — die Meldung
    erreicht den Nutzer trotzdem, statt lautlos zu verschwinden."""
    svc = _make_service()

    def _raise(*a, **kw):
        raise TimeoutError()

    with patch("app.services.user_lifecycle.resilient_session", side_effect=_raise):
        await svc._alert_sweep_down(_SWEEP_ALERT_THRESHOLD)

    svc.redis.client.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_alert_writes_notification_even_without_redis():
    svc = _make_service(with_redis=False)
    session = _FakeSession()

    with patch(
        "app.services.user_lifecycle.resilient_session",
        return_value=session,
    ):
        await svc._alert_sweep_down(_SWEEP_ALERT_THRESHOLD)

    assert len(session.added) == 1  # Notification-Schreiben ist unabhaengig von redis


@pytest.mark.asyncio
async def test_ohne_zeitstempel_bleibt_die_geschaetzte_dauer_als_rueckfall():
    svc = _make_service()
    session = _FakeSession()
    with patch("app.services.user_lifecycle.resilient_session", return_value=session):
        await svc._alert_sweep_down(_SWEEP_ALERT_THRESHOLD)
    notif = session.added[0]
    assert "~2.0 Minuten" in notif.message


@pytest.mark.asyncio
async def test_mit_zeitstempel_wird_die_echte_dauer_gemeldet():
    """Dieselbe #719-Lehre wie beim Scheduler: die Wanduhrzeit zaehlt, nicht
    streak*60s — unter DB-Fehlern kann ein Tick laenger dauern als die
    nominale Taktzeit."""
    from datetime import datetime, timedelta, timezone

    svc = _make_service()
    session = _FakeSession()
    vor_3h = datetime.now(timezone.utc) - timedelta(hours=3, minutes=2)
    with patch("app.services.user_lifecycle.resilient_session", return_value=session):
        await svc._alert_sweep_down(_SWEEP_ALERT_THRESHOLD, first_fail_at=vor_3h)
    notif = session.added[0]
    assert "~2.0 Minuten" not in notif.message
    assert "182" in notif.message  # ~3h02min in Minuten


@pytest.mark.asyncio
async def test_run_loop_alerts_after_threshold_and_recovers_silently():
    svc = _make_service()
    svc._running = True

    # Erste _SWEEP_ALERT_THRESHOLD Ticks schlagen fehl, danach ein Erfolg,
    # dann stoppt der Test die Schleife.
    calls = {"n": 0}

    async def _sweep_side_effect():
        calls["n"] += 1
        if calls["n"] <= _SWEEP_ALERT_THRESHOLD:
            raise TimeoutError()
        svc._running = False  # nach dem ersten Erfolg beenden
        return None

    svc._sweep = AsyncMock(side_effect=_sweep_side_effect)
    svc._alert_sweep_down = AsyncMock()

    with patch("app.services.user_lifecycle.asyncio.sleep", new=AsyncMock()):
        await svc.run()

    svc._alert_sweep_down.assert_awaited_once()
    streak_arg = svc._alert_sweep_down.await_args.args[0]
    assert streak_arg == _SWEEP_ALERT_THRESHOLD
    # Nach dem Erfolg ist der Streak zurueckgesetzt — kein Alt-Zustand haengt nach.
    assert svc._sweep_fail_streak == 0
    assert svc._sweep_first_fail_at is None


@pytest.mark.asyncio
async def test_run_loop_reescalates_on_doubling_not_once_per_episode():
    """Dieselbe #719-Lehre wie fuer den Scheduler: ein laenger dauernder
    Ausfall soll MEHR Aufmerksamkeit bekommen, nicht nach der ersten Meldung
    verstummen."""
    svc = _make_service()
    svc._running = True

    calls = {"n": 0}
    TOTAL_FAILS = _SWEEP_ALERT_THRESHOLD * 2  # ueberschreitet die Verdopplungs-Schwelle

    async def _sweep_side_effect():
        calls["n"] += 1
        if calls["n"] <= TOTAL_FAILS:
            raise TimeoutError()
        svc._running = False
        return None

    svc._sweep = AsyncMock(side_effect=_sweep_side_effect)
    alarme = []
    svc._alert_sweep_down = AsyncMock(side_effect=lambda streak, *a, **kw: alarme.append(streak))

    with patch("app.services.user_lifecycle.asyncio.sleep", new=AsyncMock()):
        await svc.run()

    assert alarme == [_SWEEP_ALERT_THRESHOLD, TOTAL_FAILS]


@pytest.mark.asyncio
async def test_a_single_blip_self_heals_without_any_alert():
    svc = _make_service()
    svc._running = True

    calls = {"n": 0}

    async def _sweep_side_effect():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError()
        svc._running = False
        return None

    svc._sweep = AsyncMock(side_effect=_sweep_side_effect)
    svc._alert_sweep_down = AsyncMock()

    with patch("app.services.user_lifecycle.asyncio.sleep", new=AsyncMock()):
        await svc.run()

    svc._alert_sweep_down.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_transient_errors_still_use_the_plain_log_path_not_the_alert():
    """Ein Programmierfehler (z.B. AttributeError) ist keine DB-Stoerung —
    er soll den bestehenden generischen Fehlerpfad nehmen, nicht die neue
    Eskalation ausloesen (sonst wuerde ein Bug als 'DB down' fehlgedeutet)."""
    svc = _make_service()
    svc._running = True

    calls = {"n": 0}

    async def _sweep_side_effect():
        calls["n"] += 1
        if calls["n"] == 1:
            raise AttributeError("boom")
        svc._running = False
        return None

    svc._sweep = AsyncMock(side_effect=_sweep_side_effect)
    svc._alert_sweep_down = AsyncMock()

    with patch("app.services.user_lifecycle.asyncio.sleep", new=AsyncMock()):
        await svc.run()

    svc._alert_sweep_down.assert_not_awaited()
    assert svc._sweep_fail_streak == 0


def test_transient_db_errors_matches_the_scheduler_error_classes():
    from app.services.scheduler_service import _TRANSIENT_DB_ERRORS as scheduler_classes

    assert set(_TRANSIENT_DB_ERRORS) == set(scheduler_classes)


def test_the_transient_warning_logs_the_exception_type():
    """Dieselbe #719-Lehre wie beim Scheduler: ConnectionError()/TimeoutError()
    ohne Argument geben bei str() "" zurueck — type(e).__name__ muss mit in
    der Meldung stehen, sonst ist aus dem Log nicht zu erkennen, was wirklich
    scheiterte."""
    import inspect
    from app.services import user_lifecycle as mod

    src = inspect.getsource(mod)
    block = src.split("async def run(self)", 1)[1].split("def stop(self)", 1)[0]
    assert "Sweep DB unavailable" in block
    assert "type(e).__name__" in block


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
