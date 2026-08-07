"""A persisted --resume session created under one model must not be silently
resumed under a different one — the CLI can hang forcing --resume + --model of a
different family/version. ChatConsumer._get_or_create_handler must drop the stale
pointer instead, and _persist_session must record which model a session ran under."""

import json

import pytest

from app.chat_consumer import ChatConsumer


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, _ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_persist_then_restore_same_model():
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()
    handler = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")
    handler.session_id = "sess-abc"
    await consumer._persist_session("webapp:s1", handler, "claude-sonnet-5")

    consumer._handlers.clear()  # simulate a fresh container/process
    restored = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")
    assert restored.session_id == "sess-abc"


@pytest.mark.asyncio
async def test_model_change_drops_stale_resume_session():
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()
    handler = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-4-6")
    handler.session_id = "sess-old"
    await consumer._persist_session("webapp:s1", handler, "claude-sonnet-4-6")

    consumer._handlers.clear()
    restored = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")
    assert restored.session_id is None
    # the stale pointer is dropped, not just skipped, so it can't resurface later
    assert await consumer.redis.get("agent:agent-1:claude_session:webapp:s1") is None


@pytest.mark.asyncio
async def test_legacy_bare_string_session_still_restores():
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()
    await consumer.redis.setex(
        "agent:agent-1:claude_session:webapp:s1", 0, "sess-legacy"
    )
    restored = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")
    assert restored.session_id == "sess-legacy"


@pytest.mark.asyncio
async def test_persisted_value_is_json_with_model():
    consumer = ChatConsumer("agent-1")
    consumer.redis = FakeRedis()
    handler = await consumer._get_or_create_handler("webapp:s1", "claude-sonnet-5")
    handler.session_id = "sess-abc"
    await consumer._persist_session("webapp:s1", handler, "claude-sonnet-5")

    raw = await consumer.redis.get("agent:agent-1:claude_session:webapp:s1")
    assert json.loads(raw) == {"session_id": "sess-abc", "model": "claude-sonnet-5"}
