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
