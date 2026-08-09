"""Der EINE Weg, auf dem ein Wissenseintrag entsteht.

Diesen Ablauf — nach Titel im Vault des Besitzers suchen, anlegen oder ergaenzen,
einbetten, semantisch verknuepfen — gab es viermal fast gleich: in ``api/knowledge.py``,
in ``reflection_service._apply_knowledge`` (dessen Docstring sogar „mirrors
api/knowledge.py" sagte), in der Wochensynthese und beim Auto-Capture.

Vier Kopien heissen vier Stellen, an denen das Einbetten vergessen werden kann — und
ein Eintrag ohne Embedding taucht in keiner semantischen Suche und in keinem Graphen
auf. Er ist da und trotzdem unsichtbar, was schlimmer ist als gar nicht angelegt.

Deshalb hier gebuendelt. Wer einen Wissenseintrag schreibt, ruft ``write_entry``.
"""

import logging

from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeEntry

logger = logging.getLogger(__name__)


async def embed_and_link(db: AsyncSession, entry_id: int, user_id: str | None,
                         text_to_embed: str) -> bool:
    """Embedding schreiben und den Eintrag in den Graphen haengen.

    Best effort: faellt der Embedding-Dienst aus, bleibt der Eintrag bestehen — er
    ist dann nur (noch) nicht semantisch auffindbar. Gibt zurueck, ob es geklappt hat,
    damit Aufrufer das melden koennen, statt es stillschweigend zu schlucken.
    """
    try:
        from app.services.brain_linker import auto_link
        from app.services.embedding_service import get_embedding_service

        svc = get_embedding_service()
        if not svc.enabled:
            return False
        emb = await svc.embed(text_to_embed)
        if emb is None:
            return False
        await db.execute(
            sa_text("UPDATE knowledge_entries SET embedding = CAST(:emb AS vector) WHERE id = :id"),
            {"emb": str(emb), "id": entry_id},
        )
        await db.commit()
        if user_id:
            await auto_link(entry_id, user_id, db)
        return True
    except Exception as e:  # noqa: BLE001 — nie den Schreibvorgang kippen
        logger.warning("Wissenseintrag %s konnte nicht eingebettet/verknuepft werden: %s",
                       entry_id, e)
        return False


async def write_entry(
    db: AsyncSession,
    *,
    user_id: str | None,
    title: str,
    content: str,
    tags: list[str] | None = None,
    author: str,
    merge_tags: bool = True,
) -> tuple[KnowledgeEntry, bool]:
    """Wissenseintrag anlegen oder ergaenzen, einbetten, verknuepfen.

    Gibt ``(Eintrag, war_neu)`` zurueck.

    Die Suche nach dem Titel ist IMMER auf den Vault des Besitzers eingeschraenkt:
    ohne diese Einschraenkung ueberschreibt ein gleichlautender Titel stillschweigend
    den Eintrag eines anderen Mandanten.
    """
    entry = (await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.title == title,
            KnowledgeEntry.user_id == user_id,
        )
    )).scalar_one_or_none()

    new_tags = list(tags or [])
    created = entry is None
    if entry:
        entry.content = content
        entry.updated_by = author
        if new_tags:
            entry.tags = sorted({*(entry.tags or []), *new_tags}) if merge_tags else new_tags
    else:
        entry = KnowledgeEntry(
            title=title, content=content, tags=new_tags,
            created_by=author, updated_by=author, user_id=user_id,
        )
        db.add(entry)

    await db.commit()
    await db.refresh(entry)
    await embed_and_link(db, entry.id, user_id, f"{title}: {content}")
    return entry, created
