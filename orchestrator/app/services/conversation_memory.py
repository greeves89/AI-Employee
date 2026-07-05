"""Conversation memory — auto-persist chat exchanges as EMBEDDED long-term memory.

Every user↔agent exchange across ALL channels (web, iOS, Telegram, voice) is stored
as an ``AgentMemory`` (category="conversation", room="chat:{channel}") with an
embedding, so it becomes semantically searchable. The agent's ``memory_search`` and
the voice bot's ``search_knowledge`` then recall past conversations across channels —
"worüber haben wir beim Video gesprochen?" finds it, no matter where it was said.

Best-effort: trivial exchanges are skipped; embedding uses the shared embedding
service (local bge-m3 or the OpenAI fallback); search degrades to keyword if absent.
"""

import logging
import uuid

from sqlalchemy import text as _sql_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def save_conversation_memory(
    db: AsyncSession,
    agent_id: str,
    session_id: str | None,
    channel: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """Store one exchange as an embedded conversation memory. Never raises."""
    from app.models.memory import AgentMemory
    from app.services.embedding_service import get_embedding_service

    ut = (user_text or "").strip()
    at = (assistant_text or "").strip()
    if len(ut) < 4 and len(at) < 30:          # nothing worth remembering
        return
    ch = (channel or "chat").split(":")[0]     # web / webapp / ios / telegram / voice
    content = (f"[Gespräch · {ch}] Nutzer: {ut}\nAgent: {at}")[:4000]
    key = f"chat:{session_id or ch}:{uuid.uuid4().hex[:8]}"
    try:
        mem = AgentMemory(
            agent_id=agent_id, category="conversation", key=key, content=content,
            importance=2, room=f"chat:{ch}", confidence=1.0,
        )
        db.add(mem)
        await db.commit()
        await db.refresh(mem)
        svc = get_embedding_service()
        emb = await svc.embed(content)
        if emb is not None:
            await db.execute(
                _sql_text("UPDATE agent_memories SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                {"emb": str(emb), "id": mem.id},
            )
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("[ConvMemory] save failed (%s): %s", ch, e)
