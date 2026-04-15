"""Memory compressor — per-room LLM summaries (issue #24, Should-Have).

Given an agent + room, fetches the memories in that room (excluding
superseded ones) and produces a short (<= 150 tokens) "scene summary"
via Haiku. The summary is cached in Redis for 1 hour.

Falls back to a deterministic concatenation if no LLM is configured —
the caller then gets SOMETHING instead of an error.

Used by:
  * GET /memory/room-summary/{agent_id}?room=...
  * preload_critical_memories() to give the agent a "TL;DR" of each
    room it's about to work in, instead of dumping all items raw.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import AgentMemory
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

SUMMARY_MAX_TOKENS = 150
SUMMARY_TTL_SECONDS = 3600  # 1 hour

SUMMARY_SYSTEM_PROMPT = (
    "You produce room-summaries for an AI agent's long-term memory. "
    "Given a list of memory entries from one topic/room, output a tight "
    "paragraph (max 150 tokens) that captures the current state, key "
    "decisions, and unresolved questions. No preamble, no disclaimers, "
    "just the summary. German or English, matching the source language."
)


class MemoryCompressor:
    def __init__(self, db: AsyncSession, redis: RedisService) -> None:
        self.db = db
        self.redis = redis

    async def get_or_build(self, agent_id: str, room: str, *, force: bool = False) -> dict[str, Any]:
        """Return {"summary": str, "cached": bool, "item_count": int}."""
        cache_key = f"memory:summary:{agent_id}:{room}"
        if not force:
            try:
                cached = await self.redis.client.get(cache_key)
                if cached:
                    value = cached.decode() if isinstance(cached, bytes) else cached
                    return {"summary": value, "cached": True, "item_count": -1}
            except Exception:
                pass

        # Fetch up to 50 non-superseded memories in the room
        stmt = (
            select(AgentMemory)
            .where(
                and_(
                    AgentMemory.agent_id == agent_id,
                    AgentMemory.room == room,
                    AgentMemory.superseded_by.is_(None),
                )
            )
            .order_by(AgentMemory.importance.desc(), AgentMemory.created_at.desc())
            .limit(50)
        )
        result = await self.db.execute(stmt)
        memories = list(result.scalars().all())

        if not memories:
            return {"summary": f"No memories in room '{room}' yet.", "cached": False, "item_count": 0}

        summary_text = await self._llm_summarize(memories) or self._fallback_summarize(memories)

        try:
            await self.redis.client.setex(cache_key, SUMMARY_TTL_SECONDS, summary_text)
        except Exception:
            pass

        return {"summary": summary_text, "cached": False, "item_count": len(memories)}

    async def invalidate(self, agent_id: str, room: str | None = None) -> None:
        """Drop the cached summary for one room, or all rooms if room is None."""
        if room:
            try:
                await self.redis.client.delete(f"memory:summary:{agent_id}:{room}")
            except Exception:
                pass
            return
        # Delete all summaries for this agent
        try:
            pattern = f"memory:summary:{agent_id}:*"
            async for k in self.redis.client.scan_iter(match=pattern):
                await self.redis.client.delete(k)
        except Exception:
            pass

    def _fallback_summarize(self, memories: list[AgentMemory]) -> str:
        """Deterministic, no-LLM fallback — just a tight concatenation."""
        parts = []
        for m in memories[:10]:
            parts.append(f"• {m.key}: {m.content[:120]}")
        return f"({len(memories)} memories) " + " ".join(parts)[:1200]

    async def _llm_summarize(self, memories: list[AgentMemory]) -> str | None:
        """Call Haiku via the existing agent_manager credentials.

        Returns None if no LLM is configured — caller falls back to
        deterministic summarization.
        """
        try:
            from app.config import settings
            anthropic_key = getattr(settings, "anthropic_api_key", None) or ""
        except Exception:
            anthropic_key = ""
        if not anthropic_key:
            return None

        try:
            import httpx
        except ImportError:
            return None

        body_lines = []
        for m in memories[:40]:
            body_lines.append(f"- [{m.key}] {m.content[:300]} (importance={m.importance})")
        user_msg = "Memories:\n" + "\n".join(body_lines)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": SUMMARY_MAX_TOKENS,
                        "system": SUMMARY_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_msg}],
                    },
                )
                if resp.status_code != 200:
                    logger.warning(f"[MemoryCompressor] Haiku returned {resp.status_code}")
                    return None
                data = resp.json()
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        return block.get("text", "").strip() or None
                return None
        except Exception as e:
            logger.warning(f"[MemoryCompressor] LLM call failed: {e}")
            return None
