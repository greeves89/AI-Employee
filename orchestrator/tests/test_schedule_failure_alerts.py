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
