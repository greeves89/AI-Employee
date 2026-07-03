"""Shared delegation bridge: push a message to an agent's chat queue and collect
the streamed answer.

This is the single seam both voice paths use to hand work to the container agent:
- the staged STT→LLM→TTS pipeline (``VoiceSession._delegate_to_container``)
- the Nova Sonic realtime front (``RealtimeVoiceSession`` — the ``ask_agent`` tool)

It mirrors the WS chat handler exactly: LPUSH to ``agent:{id}:chat`` and read the
per-message stream on ``agent:{id}:chat:response`` until ``done``. No new agent
mechanism — the agent can't tell whether it's answering text chat, the staged
voice pipeline, or Nova Sonic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


async def ask_agent_via_chat(
    redis: RedisService,
    agent_id: str,
    text: str,
    *,
    source: str = "realtime_voice",
    timeout: float = 90.0,
    chat_session_id: str | None = None,
    on_event=None,
) -> str:
    """Send ``text`` to the agent and return its full text answer (or an error marker).

    Blocks until the agent publishes ``done`` for this message, or ``timeout``.
    """
    if not (redis and redis.client):
        return "[Fehler: Redis nicht verfügbar]"

    message_id = uuid.uuid4().hex[:12]
    payload: dict = {
        "id": message_id,
        "text": text,
        "model": None,
        "images": [],
        "source": source,
    }
    if chat_session_id:
        payload["chat_session_id"] = chat_session_id

    channel = f"agent:{agent_id}:chat:response"
    pubsub = await redis.subscribe(channel)
    await redis.client.lpush(f"agent:{agent_id}:chat", json.dumps(payload))

    collected: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    try:
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if not msg or msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                evt = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                continue
            if evt.get("message_id") != message_id:
                continue
            etype = evt.get("type")
            edata = evt.get("data") or {}
            if on_event is not None and etype in ("tool_call", "tool_result", "text", "image", "file"):
                try:
                    await on_event(etype, edata)
                except Exception:  # noqa: BLE001
                    logger.debug("on_event callback error", exc_info=True)
            if etype == "text":
                collected.append(str(edata.get("text", "")))
            elif etype == "done":
                break
            elif etype == "error":
                return f"[Fehler: {edata.get('message', 'unbekannt')}]"
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("chat bridge pubsub cleanup error", exc_info=True)
    return "".join(collected).strip()
