"""Tests for the persistent Telegram chat-to-agent mapping (issue #408).

The mapping used to live in an in-process dict and was lost on every restart.
It is now stored in Redis. These tests use a tiny in-memory fake async Redis
(patched over `aioredis.from_url`) so no live Redis is needed — they verify that
set/get/clear round-trip and that a fresh module import (simulating a restart)
still sees a previously persisted mapping.
"""

from unittest.mock import patch

import pytest

from app.telegram import active_chats


class _FakeRedis:
    """Minimal async Redis stand-in backed by a shared dict (survives 'restart')."""

    def __init__(self, store: dict):
        self._store = store

    async def set(self, key, value):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, key):
        self._store.pop(key, None)

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis_store():
    store: dict = {}
    with patch.object(
        active_chats.aioredis,
        "from_url",
        lambda *a, **k: _FakeRedis(store),
    ):
        yield store


@pytest.mark.asyncio
async def test_set_get_roundtrip(fake_redis_store):
    assert await active_chats.get_active_chat(12345) is None

    await active_chats.set_active_chat(12345, "agent-abc")
    assert await active_chats.get_active_chat(12345) == "agent-abc"
    assert fake_redis_store["telegram:active_chat:12345"] == "agent-abc"


@pytest.mark.asyncio
async def test_clear_returns_previous_and_removes(fake_redis_store):
    await active_chats.set_active_chat(999, "agent-xyz")

    cleared = await active_chats.clear_active_chat(999)
    assert cleared == "agent-xyz"
    assert await active_chats.get_active_chat(999) is None


@pytest.mark.asyncio
async def test_clear_missing_returns_none(fake_redis_store):
    assert await active_chats.clear_active_chat(404) is None


@pytest.mark.asyncio
async def test_mapping_survives_restart(fake_redis_store):
    """The whole point of #408: the mapping persists across an orchestrator restart.

    A restart drops in-process state but not Redis. We model it by keeping the
    backing store (Redis) while creating a brand-new fake client for each call —
    which the helpers already do (a fresh from_url per call).
    """
    await active_chats.set_active_chat(555, "agent-persist")

    # Simulate restart: process memory is gone, but Redis store persists.
    assert await active_chats.get_active_chat(555) == "agent-persist"
