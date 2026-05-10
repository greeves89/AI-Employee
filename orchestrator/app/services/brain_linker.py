"""Second Brain auto-linker — creates semantic links between knowledge entries."""

import logging
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75
MAX_LINKS_PER_ENTRY = 10


async def auto_link(entry_id: int, user_id: str, db: AsyncSession) -> int:
    """Compute semantic links for a knowledge entry against all other entries of the same user.

    Called after a knowledge entry is created or updated with a fresh embedding.
    Returns the number of links created.
    """
    try:
        from app.services.embedding_service import get_embedding_service
        from app.models.brain import BrainLink
        from sqlalchemy import text

        svc = get_embedding_service()
        if not svc.enabled:
            return 0

        # Get the source entry's embedding
        row = (await db.execute(
            text("SELECT embedding FROM knowledge_entries WHERE id = :id AND embedding IS NOT NULL"),
            {"id": entry_id},
        )).fetchone()

        if not row or row[0] is None:
            return 0

        source_vec = row[0]

        # Find top similar entries for this user (excluding self)
        candidates = (await db.execute(
            text("""
                SELECT id, title,
                       1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                FROM knowledge_entries
                WHERE user_id = :uid
                  AND id != :entry_id
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT :limit
            """),
            {"vec": str(source_vec), "uid": user_id, "entry_id": entry_id, "limit": MAX_LINKS_PER_ENTRY},
        )).fetchall()

        created = 0
        for target_id, title, similarity in candidates:
            if similarity < SIMILARITY_THRESHOLD:
                break

            # Upsert: ignore if link already exists in either direction
            existing = await db.execute(
                select(BrainLink).where(
                    ((BrainLink.source_id == entry_id) & (BrainLink.target_id == target_id))
                    | ((BrainLink.source_id == target_id) & (BrainLink.target_id == entry_id))
                )
            )
            if existing.scalar_one_or_none():
                continue

            link = BrainLink(
                user_id=user_id,
                source_id=entry_id,
                target_id=target_id,
                similarity=round(float(similarity), 4),
                auto_generated=True,
            )
            db.add(link)
            created += 1

        if created:
            await db.commit()
            logger.debug(f"brain_linker: {created} links for entry {entry_id}")

        return created

    except Exception as e:
        logger.warning(f"brain_linker auto_link failed for entry {entry_id}: {e}")
        return 0


async def remove_links(entry_id: int, db: AsyncSession) -> None:
    """Remove all brain links for a deleted knowledge entry."""
    from app.models.brain import BrainLink
    await db.execute(
        delete(BrainLink).where(
            (BrainLink.source_id == entry_id) | (BrainLink.target_id == entry_id)
        )
    )
    await db.commit()
