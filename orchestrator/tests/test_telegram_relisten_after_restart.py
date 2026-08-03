"""Regression test for the #408 follow-up: re-establish the response listener.

The chat-to-agent mapping persists in Redis (issue #408), but the per-chat
response listener is a process-local ``asyncio.Task`` that is lost on restart.
Before this fix, after a restart the bot would accept a message on a persisted
chat (mapping still present) and forward it to the agent, but nobody was
listening on ``agent:<id>:chat:response`` — so the agent's reply never reached
Telegram and the bot appeared to hang silently.

These tests verify that a message on a persisted chat with NO live listener
lazily (re)starts one, and that an already-running listener is left untouched.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.handlers import commands


@pytest.fixture(autouse=True)
def _clear_listeners():
    commands._chat_listeners.clear()
    yield
    for task in commands._chat_listeners.values():
        task.cancel()
    commands._chat_listeners.clear()


def _make_update(chat_id: int, text: str = "hello"):
    """Build a minimal fake telegram Update for handle_message."""
    bot = object()
    message = AsyncMock()
    message.text = text
    message.message_id = 1
    message.get_bot = lambda: bot  # get_bot is synchronous in python-telegram-bot

    update = AsyncMock()
    update.effective_chat.id = chat_id
    update.effective_user.username = "grevvy"
    update.effective_user.first_name = "Grevvy"
    update.message = message
    return update


@pytest.mark.asyncio
async def test_message_on_persisted_chat_restarts_listener():
    chat_id = 4242
    # Simulate post-restart state: mapping persists, no in-process listener.
    assert chat_id not in commands._chat_listeners

    async def _noop_listener(bot, cid, aid):
        await asyncio.sleep(3600)

    with patch.object(commands, "get_active_chat", AsyncMock(return_value="agent-1")), \
         patch.object(commands, "_listen_agent_responses", _noop_listener), \
         patch.object(commands.aioredis, "from_url") as from_url:
        redis = AsyncMock()
        from_url.return_value = redis

        update = _make_update(chat_id)
        await commands.handle_message(update, None)

        # The listener must have been lazily (re)established.
        assert chat_id in commands._chat_listeners
        assert not commands._chat_listeners[chat_id].done()
        # And the message was still forwarded to the agent.
        redis.lpush.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_live_listener_is_not_replaced():
    chat_id = 7777

    async def _noop_listener(bot, cid, aid):
        await asyncio.sleep(3600)

    with patch.object(commands, "get_active_chat", AsyncMock(return_value="agent-2")), \
         patch.object(commands, "_listen_agent_responses", _noop_listener), \
         patch.object(commands.aioredis, "from_url") as from_url:
        from_url.return_value = AsyncMock()

        # Prime a live listener as if /chat had already been issued this process.
        commands._ensure_listener(object(), chat_id, "agent-2", restart=True)
        first = commands._chat_listeners[chat_id]

        update = _make_update(chat_id)
        await commands.handle_message(update, None)

        # Same task object — a healthy listener must not be churned per message.
        assert commands._chat_listeners[chat_id] is first


@pytest.mark.asyncio
async def test_dead_listener_is_replaced():
    chat_id = 9001

    async def _noop_listener(bot, cid, aid):
        await asyncio.sleep(3600)

    with patch.object(commands, "_listen_agent_responses", _noop_listener):
        # A finished (dead) task stands in for a listener whose loop exited.
        dead = asyncio.create_task(asyncio.sleep(0))
        await dead
        commands._chat_listeners[chat_id] = dead

        commands._ensure_listener(object(), chat_id, "agent-3")

        assert commands._chat_listeners[chat_id] is not dead
        assert not commands._chat_listeners[chat_id].done()
