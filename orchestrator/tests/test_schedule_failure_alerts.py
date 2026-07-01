"""Tests for schedule-failure Telegram alerts.

When a recurring schedule's task fails, the operator should be notified
immediately via Telegram instead of discovering the missing artifact hours
later. See task_router._alert_schedule_failure and
scheduler_service._tick_failure_watchdog.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_failed_scheduled_task_publishes_telegram_alert():
    """A failed task whose metadata carries a schedule_id triggers a
    Telegram alert on the `telegram:notification` Redis channel."""
    from app.core.task_router import TaskRouter
    from app.models.schedule import Schedule

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    router = TaskRouter.__new__(TaskRouter)
    router.redis = redis
    router.db = AsyncMock()

    schedule = Schedule(
        id="abcd1234",
        name="Morgen-Podcast 06:00",
        prompt="generate today's podcast",
        interval_seconds=0,
        cron_expression="55 3 * * *",
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
        total_runs=12,
        success_count=10,
        fail_count=1,  # will become 2 after this run
    )

    scalar = MagicMock()
    scalar.scalar_one_or_none = MagicMock(return_value=schedule)
    router.db.execute = AsyncMock(return_value=scalar)

    await router._update_schedule_stats(
        "abcd1234",
        {"status": "failed", "error": "ffmpeg returned non-zero"},
    )

    assert schedule.fail_count == 2
    assert schedule.success_count == 10
    redis.client.publish.assert_awaited_once()
    channel, raw = redis.client.publish.await_args.args
    assert channel == "telegram:notification"
    payload = json.loads(raw)
    assert "Morgen-Podcast 06:00" in payload["text"]
    assert "fail_count=2" in payload["text"]
    assert "ffmpeg returned non-zero" in payload["text"]


@pytest.mark.asyncio
async def test_successful_scheduled_task_does_not_alert():
    """Happy path stays quiet — no Telegram noise on success."""
    from app.core.task_router import TaskRouter
    from app.models.schedule import Schedule

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    router = TaskRouter.__new__(TaskRouter)
    router.redis = redis
    router.db = AsyncMock()

    schedule = Schedule(
        id="abcd1234",
        name="Morgen-Podcast 06:00",
        prompt="generate today's podcast",
        interval_seconds=0,
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
        total_runs=10,
        success_count=9,
        fail_count=1,
    )

    scalar = MagicMock()
    scalar.scalar_one_or_none = MagicMock(return_value=schedule)
    router.db.execute = AsyncMock(return_value=scalar)

    await router._update_schedule_stats("abcd1234", {"status": "completed"})

    assert schedule.success_count == 10
    redis.client.publish.assert_not_called()


@pytest.mark.asyncio
async def test_alert_swallows_redis_outage():
    """A Redis outage must not break the task-completion path."""
    from app.core.task_router import TaskRouter
    from app.models.schedule import Schedule

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock(side_effect=RuntimeError("redis down"))

    router = TaskRouter.__new__(TaskRouter)
    router.redis = redis
    router.db = AsyncMock()

    schedule = Schedule(
        id="abcd1234",
        name="Morgen-Podcast 06:00",
        prompt="x",
        interval_seconds=0,
        next_run_at=datetime.now(timezone.utc),
        total_runs=1,
        success_count=0,
        fail_count=0,
    )

    # Should not raise.
    await router._alert_schedule_failure(schedule, {"status": "failed", "error": "x"})


@pytest.mark.asyncio
async def test_alert_handles_missing_redis_client():
    """No Redis client → silently no-op (e.g. tests, degraded mode)."""
    from app.core.task_router import TaskRouter
    from app.models.schedule import Schedule

    redis = MagicMock()
    redis.client = None  # not initialised

    router = TaskRouter.__new__(TaskRouter)
    router.redis = redis
    router.db = AsyncMock()

    schedule = Schedule(
        id="abcd1234",
        name="x",
        prompt="x",
        interval_seconds=0,
        next_run_at=datetime.now(timezone.utc),
        total_runs=1,
        success_count=0,
        fail_count=0,
    )

    await router._alert_schedule_failure(schedule, {"status": "failed"})
    # No raise, no publish. Pass.


# ---------------------------------------------------------------------------
# _tick_failure_watchdog tests
# ---------------------------------------------------------------------------

def _make_watchdog_service(redis_mock):
    from app.services.scheduler_service import SchedulerService

    svc = SchedulerService.__new__(SchedulerService)
    svc.redis = redis_mock
    svc._failure_watchdog_last_run = None
    svc._watchdog_alerted = {}
    return svc


def _stale_schedule(schedule_id="sched-1", name="Daily Report", total=10, ok=7, fail=1):
    from datetime import datetime, timedelta, timezone
    from app.models.schedule import Schedule

    now = datetime.now(timezone.utc)
    return Schedule(
        id=schedule_id,
        name=name,
        prompt="x",
        interval_seconds=86400,
        next_run_at=now + timedelta(days=1),
        last_run_at=now - timedelta(hours=5),
        total_runs=total,
        success_count=ok,
        fail_count=fail,
    )


def _mock_db_ctx(schedules):
    from unittest.mock import AsyncMock, MagicMock

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = schedules
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


@pytest.mark.asyncio
async def test_watchdog_fires_on_stale_drift():
    """Watchdog alerts when drift≥2 and last_run_at > 2h ago."""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    svc = _make_watchdog_service(redis)
    schedule = _stale_schedule(total=10, ok=7, fail=1)  # drift=2

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_awaited_once()
    channel, raw = redis.client.publish.await_args.args
    assert channel == "telegram:notification"
    payload = json.loads(raw)
    assert "Daily Report" in payload["text"]
    assert svc._watchdog_alerted["sched-1"] == 2


@pytest.mark.asyncio
async def test_watchdog_no_re_alert_on_same_drift():
    """Watchdog stays silent when drift has not increased since last alert."""
    from unittest.mock import AsyncMock, MagicMock, patch

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    svc = _make_watchdog_service(redis)
    svc._watchdog_alerted = {"sched-1": 2}  # already alerted at drift=2
    schedule = _stale_schedule(total=10, ok=7, fail=1)  # drift still 2

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_re_alerts_when_drift_increases():
    """Watchdog fires again when drift grows beyond the previously alerted value."""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    svc = _make_watchdog_service(redis)
    svc._watchdog_alerted = {"sched-1": 2}  # previously alerted at drift=2
    schedule = _stale_schedule(total=11, ok=7, fail=1)  # drift now=3

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_awaited_once()
    assert svc._watchdog_alerted["sched-1"] == 3


@pytest.mark.asyncio
async def test_watchdog_skips_small_drift():
    """Watchdog does not alert when drift < 2 (single in-flight task noise)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    svc = _make_watchdog_service(redis)
    schedule = _stale_schedule(total=10, ok=9, fail=0)  # drift=1

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_skips_recent_last_run():
    """Watchdog stays silent when stale_for < 2h (run was recent, not truly stuck)."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.models.schedule import Schedule

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    svc = _make_watchdog_service(redis)
    now = datetime.now(timezone.utc)
    schedule = Schedule(
        id="sched-fresh",
        name="Fresh Schedule",
        prompt="x",
        interval_seconds=3600,
        next_run_at=now + timedelta(hours=1),
        last_run_at=now - timedelta(minutes=30),  # only 30 min ago — not stale
        total_runs=10,
        success_count=7,
        fail_count=1,  # drift=2 but not stale enough
    )

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        await svc._tick_failure_watchdog()

    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_1h_global_throttle():
    """Watchdog skips the whole run when called again within 1h of the last run."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    redis = MagicMock()
    redis.client = AsyncMock()
    redis.client.publish = AsyncMock()

    svc = _make_watchdog_service(redis)
    # Simulate having run only 30 min ago
    svc._failure_watchdog_last_run = datetime.now(timezone.utc) - timedelta(minutes=30)
    schedule = _stale_schedule(total=10, ok=7, fail=1)  # would normally alert

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        await svc._tick_failure_watchdog()

    # DB should never even be queried; no publish
    redis.client.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_no_redis_no_crash():
    """Watchdog silently skips publishing when Redis is unavailable."""
    from unittest.mock import patch

    svc = _make_watchdog_service(redis_mock=None)
    svc.redis = None
    schedule = _stale_schedule(total=10, ok=7, fail=1)

    with patch("app.services.scheduler_service.async_session_factory", return_value=_mock_db_ctx([schedule])):
        # Must not raise
        await svc._tick_failure_watchdog()
