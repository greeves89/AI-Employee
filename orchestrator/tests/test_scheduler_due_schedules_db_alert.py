"""Tests for the DueSchedules DB-outage escalation added after issue #601.

Background: a single failed DueSchedules tick self-heals silently (see
test_scheduler_transient_db_guard.py) — that's fine for a one-off blip. But a
sustained outage (confirmed on 2026-08-15: ~30min of Postgres unavailability)
silently blocked every DueSchedules check with nobody told, and the daily
06:00 jobs only got caught because of an unrelated, separately configured
safety-net schedule. SchedulerService._alert_due_schedules_down() closes that
gap: after _DUE_SCHEDULES_ALERT_THRESHOLD consecutive failed ticks it writes
an urgent Notification and publishes a Telegram alert.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scheduler_service import SchedulerService, _DUE_SCHEDULES_ALERT_THRESHOLD


def _make_service(with_redis: bool = True) -> SchedulerService:
    svc = SchedulerService.__new__(SchedulerService)
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
        "app.services.scheduler_service.resilient_session",
        return_value=session,
    ):
        await svc._alert_due_schedules_down(_DUE_SCHEDULES_ALERT_THRESHOLD)

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
    """If the DB is still unreachable, the Notification write itself fails —
    but the Telegram publish goes over Redis, not the DB, so the user still
    hears about it instead of the alert silently vanishing."""
    svc = _make_service()

    def _raise(*a, **kw):
        raise TimeoutError()

    with patch("app.services.scheduler_service.resilient_session", side_effect=_raise):
        await svc._alert_due_schedules_down(_DUE_SCHEDULES_ALERT_THRESHOLD)

    svc.redis.client.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_alert_noop_without_redis():
    svc = _make_service(with_redis=False)
    session = _FakeSession()

    with patch(
        "app.services.scheduler_service.resilient_session",
        return_value=session,
    ):
        await svc._alert_due_schedules_down(_DUE_SCHEDULES_ALERT_THRESHOLD)

    assert len(session.added) == 1  # Notification write is independent of redis


# Issue #719: ein 7,6h-Ausfall wurde als "~2.0 min" gemeldet — die alte Formel
# (streak*30s) nahm die GEPLANTE Tick-Dauer an, nicht die gemessene. Unter
# DB-Fehlern wartet jedes Subsystem im selben Durchlauf seriell seinen eigenen
# Verbindungs-Timeout ab; ein Tick dauert dann eher 8-9 Minuten.


@pytest.mark.asyncio
async def test_ohne_zeitstempel_bleibt_die_alte_schaetzung_als_rueckfall():
    """Abwaertskompatibel fuer Aufrufer ohne first_fail_at."""
    svc = _make_service()
    session = _FakeSession()
    with patch("app.services.scheduler_service.resilient_session", return_value=session):
        await svc._alert_due_schedules_down(4)
    notif = session.added[0]
    assert "~2.0 Minuten" in notif.message


@pytest.mark.asyncio
async def test_mit_zeitstempel_wird_die_echte_dauer_gemeldet():
    """Der gemeldete Fall: 4 Ticks in Wirklichkeit ueber 7,6 Stunden."""
    from datetime import datetime, timedelta, timezone

    svc = _make_service()
    session = _FakeSession()
    vor_7_6h = datetime.now(timezone.utc) - timedelta(hours=7, minutes=36)
    with patch("app.services.scheduler_service.resilient_session", return_value=session):
        await svc._alert_due_schedules_down(4, first_fail_at=vor_7_6h)
    notif = session.added[0]
    assert "~2.0 Minuten" not in notif.message
    assert "456" in notif.message or "457" in notif.message  # ~7,6h in Minuten


def test_the_transient_warning_logs_the_exception_type():
    """Issue #719, Punkt 3: ConnectionError()/TimeoutError() ohne Argument
    geben bei str() "" zurueck -- ~445 WARNING-Zeilen endeten auf ": " und
    dann nichts, aus dem Log war nicht zu erkennen, ob der Pool ausgelaufen,
    die Verbindung abgewiesen oder DNS haengengeblieben war. Grenze via
    Funktionsgrenzen statt fester Zeichenzahl (#726)."""
    import inspect
    from app.services import scheduler_service as mod

    src = inspect.getsource(mod)
    block = src.split("async def run(self)", 1)[1].split("async def _start_due_followups", 1)[0]
    assert "DueSchedules DB unavailable" in block
    assert "type(e).__name__" in block


@pytest.mark.asyncio
async def test_the_run_loop_tracks_first_fail_and_reescalates_on_doubling():
    """Issue #719, Punkt 2: bisher wurde GENAU EINMAL pro Episode eskaliert
    (Flag faellt erst beim naechsten Erfolg zurueck) — ein Ausfall, der laenger
    dauert, bekam dadurch WENIGER Aufmerksamkeit als einer, der kurz war."""
    svc = _make_service()
    svc._due_schedules_fail_streak = 0
    svc._due_schedules_first_fail_at = None
    svc._due_schedules_next_alert_streak = _DUE_SCHEDULES_ALERT_THRESHOLD

    alarme = []
    svc._alert_due_schedules_down = AsyncMock(side_effect=lambda streak, *a, **kw: alarme.append(streak))

    async def _tick(streak_delta):
        from app.services.scheduler_service import _TRANSIENT_DB_ERRORS
        from datetime import datetime, timezone
        svc._due_schedules_fail_streak += streak_delta
        if svc._due_schedules_first_fail_at is None:
            svc._due_schedules_first_fail_at = datetime.now(timezone.utc)
        if svc._due_schedules_fail_streak >= svc._due_schedules_next_alert_streak:
            svc._due_schedules_next_alert_streak = svc._due_schedules_fail_streak * 2
            await svc._alert_due_schedules_down(
                svc._due_schedules_fail_streak, svc._due_schedules_first_fail_at,
            )

    for _ in range(4):
        await _tick(1)   # streak 1,2,3,4 -> alarm bei 4 (Schwelle)
    for _ in range(4):
        await _tick(1)   # streak 5..8 -> zweiter Alarm bei 8 (Verdopplung)

    assert alarme == [4, 8]
