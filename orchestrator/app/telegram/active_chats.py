"""Persistent Telegram chat-to-agent mapping.

The mapping `chat_id -> agent_id` used to live in a plain in-process dict, so it
was dropped on every orchestrator restart (deploy, update, crash) and the user
had to re-issue /chat before the bot would respond again. It also ruled out
running more than one orchestrator replica.

We persist it in Redis, which is already part of the stack and already used by
the Telegram handlers. Keys look like `telegram:active_chat:<chat_id>` -> agent_id.
Only the chat_id -> agent_id mapping is persisted; the per-chat asyncio listener
tasks (`_chat_listeners`) are genuinely process-local and stay in memory.
"""

import redis.asyncio as aioredis

from app.config import settings

_KEY_PREFIX = "telegram:active_chat:"


def _key(chat_id: int) -> str:
    return f"{_KEY_PREFIX}{chat_id}"


async def set_active_chat(chat_id: int, agent_id: str) -> None:
    """Persist the agent a Telegram chat is talking to."""
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.set(_key(chat_id), agent_id)
    finally:
        await redis.aclose()


async def get_active_chat(chat_id: int) -> str | None:
    """Return the agent_id a chat is bound to, or None if no active chat."""
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        return await redis.get(_key(chat_id))
    finally:
        await redis.aclose()


async def clear_active_chat(chat_id: int) -> str | None:
    """Remove a chat's mapping. Returns the agent_id that was set, or None."""
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        agent_id = await redis.get(_key(chat_id))
        await redis.delete(_key(chat_id))
        return agent_id
    finally:
        await redis.aclose()
