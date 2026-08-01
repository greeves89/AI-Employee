"""Second Brain auto-linker for AGENT MEMORIES (issue #157, memory half).

Mirrors ``brain_linker`` (which links knowledge entries). Populates
``agent_memory_links`` with ``relation='semantic_similar'`` rows from embedding
cosine similarity, so the previously-never-filled memory graph becomes a real
knowledge-graph and the ``/memory/{id}/related`` endpoint has data.

Scope: memories of the SAME agent (memories are agent-scoped). Manual links
(written via memory_save with an explicit relation) are respected — a pair that
already has ANY link in either direction is skipped, and semantic links are added
with a distinct relation so they never overwrite a manual one.
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75
MAX_LINKS_PER_MEMORY = 10
RELATION = "semantic_similar"


async def link_memory(memory_id: int, agent_id: str, db: AsyncSession) -> int:
    """Create semantic links for one memory against other memories of the same agent.

    Returns the number of links created. Fire-and-forget: never raises.
    """
    try:
        from app.services.embedding_service import get_embedding_service
        from app.models.memory import AgentMemoryLink

        svc = get_embedding_service()
        if not svc.enabled:
            return 0

        row = (await db.execute(
            text("SELECT embedding FROM agent_memories WHERE id = :id AND embedding IS NOT NULL"),
            {"id": memory_id},
        )).fetchone()
        if not row or row[0] is None:
            return 0
        source_vec = row[0]

        # Nearest other memories of the SAME agent (exclude self + superseded).
        candidates = (await db.execute(
            text("""
                SELECT id, 1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                FROM agent_memories
                WHERE agent_id = :aid
                  AND id != :mid
                  AND embedding IS NOT NULL
                  AND superseded_by IS NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :limit
            """),
            {"vec": str(source_vec), "aid": agent_id, "mid": memory_id, "limit": MAX_LINKS_PER_MEMORY},
        )).fetchall()

        created = 0
        for target_id, similarity in candidates:
            if similarity is None or similarity < SIMILARITY_THRESHOLD:
                break
            # Skip if a link (any relation) already exists in either direction —
            # respects manual links and avoids duplicate semantic edges.
            exists = (await db.execute(
                select(AgentMemoryLink.source_id).where(
                    ((AgentMemoryLink.source_id == memory_id) & (AgentMemoryLink.target_id == target_id))
                    | ((AgentMemoryLink.source_id == target_id) & (AgentMemoryLink.target_id == memory_id))
                ).limit(1)
            )).first()
            if exists:
                continue
            db.add(AgentMemoryLink(
                source_id=memory_id,
                target_id=int(target_id),
                relation=RELATION,
                similarity=round(float(similarity), 4),
                auto_generated=True,
            ))
            created += 1

        if created:
            await db.commit()
            logger.debug("memory_linker: %d semantic links for memory %s", created, memory_id)
        return created
    except Exception as e:  # noqa: BLE001 — never break the save path
        logger.warning("memory_linker link_memory failed for %s: %s", memory_id, e)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


async def backfill_all(db: AsyncSession, agent_id: str | None = None) -> dict:
    """Re-run the linker for every memory that has an embedding (optionally scoped
    to one agent). Used to link the EXISTING knowledge base once. Returns stats."""
    where = "embedding IS NOT NULL AND superseded_by IS NULL"
    params: dict = {}
    if agent_id:
        where += " AND agent_id = :aid"
        params["aid"] = agent_id
    rows = (await db.execute(
        text(f"SELECT id, agent_id FROM agent_memories WHERE {where} ORDER BY id"),
        params,
    )).fetchall()
    total_links = 0
    for mem_id, aid in rows:
        total_links += await link_memory(int(mem_id), aid, db)
    return {"memories_processed": len(rows), "links_created": total_links}
