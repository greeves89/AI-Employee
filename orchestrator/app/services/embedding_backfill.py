"""Background job to backfill embeddings for existing memories + knowledge entries.

Runs once at orchestrator startup (with a delay) + can be triggered manually.
Processes memories in batches to be API-rate-limit friendly.
"""

import asyncio
import logging

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import AgentMemory
from app.models.knowledge import KnowledgeEntry
from app.services.embedding_service import get_embedding_service

logger = logging.getLogger(__name__)

BATCH_SIZE = 16  # Keep small so each chunk stays under 60s on CPU
# Wait this long after orchestrator startup before starting the backfill
INITIAL_DELAY_SECONDS = 30


async def backfill_memory_embeddings(db: AsyncSession, limit: int = BATCH_SIZE) -> int:
    """Embed up to `limit` memories that don't yet have an embedding.

    Returns the number of embeddings generated.
    """
    svc = get_embedding_service()

    # Find memories missing an embedding
    result = await db.execute(
        sa_text(
            "SELECT id, key, content FROM agent_memories "
            "WHERE embedding IS NULL ORDER BY updated_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    if not rows:
        return 0

    # Batch-embed
    texts = [f"{r['key']}: {r['content']}" for r in rows]
    embeddings = await svc.embed_batch(texts)

    # Update each row individually (pgvector needs string form)
    count = 0
    for row, emb in zip(rows, embeddings):
        if emb is None:
            continue
        await db.execute(
            sa_text("UPDATE agent_memories SET embedding = CAST(:emb AS vector) WHERE id = :id"),
            {"emb": str(emb), "id": row["id"]},
        )
        count += 1
    await db.commit()
    return count


async def backfill_knowledge_embeddings(db: AsyncSession, limit: int = BATCH_SIZE) -> int:
    """Same as above but for knowledge_entries."""
    svc = get_embedding_service()

    result = await db.execute(
        sa_text(
            "SELECT id, title, content FROM knowledge_entries "
            "WHERE embedding IS NULL ORDER BY updated_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    if not rows:
        return 0

    texts = [f"{r['title']}: {r['content']}" for r in rows]
    embeddings = await svc.embed_batch(texts)

    count = 0
    for row, emb in zip(rows, embeddings):
        if emb is None:
            continue
        await db.execute(
            sa_text("UPDATE knowledge_entries SET embedding = CAST(:emb AS vector) WHERE id = :id"),
            {"emb": str(emb), "id": row["id"]},
        )
        count += 1
    await db.commit()
    return count


async def run_backfill_loop(db_factory) -> None:
    """Background loop: keep embedding unembedded entries until all done, then idle-check every 10 min."""
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    logger.info("[EmbeddingBackfill] Starting...")

    svc = get_embedding_service()
    # Verify the local embedding service is actually reachable
    if not await svc._check_local_available():
        logger.info("[EmbeddingBackfill] Local embedding service not yet reachable — will retry later")
        # Keep the loop alive in case the service starts up later
        while True:
            await asyncio.sleep(60)
            if await svc._check_local_available():
                logger.info("[EmbeddingBackfill] Local embedding service now reachable, starting backfill")
                break

    total_memories = 0
    total_knowledge = 0
    while True:
        try:
            async with db_factory() as db:
                mem_count = await backfill_memory_embeddings(db)
                total_memories += mem_count
                kb_count = await backfill_knowledge_embeddings(db)
                total_knowledge += kb_count

            if mem_count == 0 and kb_count == 0:
                # All done — sleep longer
                logger.info(
                    f"[EmbeddingBackfill] Done. "
                    f"Total embedded: {total_memories} memories, {total_knowledge} knowledge entries"
                )
                await asyncio.sleep(600)  # Check again in 10 min
            else:
                logger.info(
                    f"[EmbeddingBackfill] Embedded {mem_count} memories + {kb_count} knowledge entries "
                    f"(total: {total_memories}+{total_knowledge})"
                )
                await asyncio.sleep(2)  # Small pause between batches
        except Exception as e:
            logger.error(f"[EmbeddingBackfill] Error: {e}")
            await asyncio.sleep(300)
