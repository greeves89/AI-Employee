"""Behavioural regression test for #569.

The two existing watchdog tests (test_chat_turn_idle_watchdog.py,
test_chat_idle_clock_starts_at_turn.py) only assertIn() on the string
"last_activity_at" — they pass as long as that text appears ANYWHERE in the
source, regardless of which object it is written to. That is exactly how the
real bug (two independent LogPublisher instances: one the watchdog reads in
_process_one, a different one the handler heartbeats into) went undetected.

This test asserts the actual invariant: the handler _get_or_create_handler()
returns must hold the SAME publisher instance the caller (the watchdog's own
_process_one) passed in. If a future change reintroduces a second, freshly
constructed LogPublisher inside _get_or_create_handler, this test catches it
by identity, not by substring.
"""

import asyncio

import pytest

from app.chat_consumer import ChatConsumer
from app.log_publisher import LogPublisher


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, _ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, *_args, **_kwargs):
        pass

    async def rpush(self, *_args, **_kwargs):
        pass

    async def ltrim(self, *_args, **_kwargs):
        pass


@pytest.mark.asyncio
async def test_handler_heartbeats_into_the_callers_publisher_not_a_fresh_one():
    """The watchdog's clock and the handler's heartbeat must be the same object."""
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()

    watchdog_publisher = object()  # sentinel — identity is all that matters here
    handler = await consumer._get_or_create_handler(
        "webapp:s1", "claude-sonnet-5", watchdog_publisher
    )

    assert handler.log_publisher is watchdog_publisher, (
        "handler.log_publisher must be the exact instance the watchdog reads "
        "last_activity_at off of, or every heartbeat during the turn is invisible "
        "to the idle check and the turn dies at the hard 600s ceiling (#569)"
    )


@pytest.mark.asyncio
async def test_cached_handler_keeps_its_original_publisher():
    """A second turn on the same channel reuses the cached handler — it must not
    silently swap in whatever publisher instance that second call happens to pass."""
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()

    first_publisher = object()
    handler = await consumer._get_or_create_handler(
        "webapp:s1", "claude-sonnet-5", first_publisher
    )

    second_publisher = object()
    same_handler = await consumer._get_or_create_handler(
        "webapp:s1", "claude-sonnet-5", second_publisher
    )

    assert same_handler is handler
    assert same_handler.log_publisher is first_publisher


@pytest.mark.asyncio
async def test_no_publisher_passed_falls_back_to_a_working_one():
    """Callers outside the watchdog path (e.g. tests, --resume bookkeeping) that
    don't pass a log_publisher must still get a usable handler, not a crash."""
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()

    handler = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")

    assert handler.log_publisher is not None
    assert hasattr(handler.log_publisher, "last_activity_at")


@pytest.mark.asyncio
async def test_handler_event_advances_the_watchdog_clock():
    """Pins the actual invariant, not just object identity: a chat event the
    handler publishes must be observable as forward motion on the exact clock
    _process_one's idle check reads. Identity alone (the two tests above) would
    still pass a future refactor that keeps a single publisher instance around
    but stops writing last_activity_at on it from the handler's code path — this
    test would catch that, the identity tests would not."""
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()

    watchdog_publisher = LogPublisher(consumer.redis, "agent-1")
    handler = await consumer._get_or_create_handler(
        "webapp:s1", "claude-sonnet-5", watchdog_publisher
    )

    before = watchdog_publisher.last_activity_at
    await asyncio.sleep(0.01)
    await handler.log_publisher.publish_chat("m1", "text", {"text": "still working"})

    assert watchdog_publisher.last_activity_at > before, (
        "a chat event from the handler must advance the clock _process_one reads"
    )
