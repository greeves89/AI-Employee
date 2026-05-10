"""Second Brain API — unified semantic knowledge graph, user-scoped.

User-facing:  GET  /brain/graph          full graph (nodes + semantic edges)
              GET  /brain/search          semantic search across full brain
              GET  /brain/related/{id}    neighbors of a knowledge entry

Agent-facing: GET  /brain/agent/search   search across user's full brain
              POST /brain/agent/contribute  add a node to the user's brain
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.brain import BrainLink
from app.models.knowledge import KnowledgeEntry

router = APIRouter(prefix="/brain", tags=["brain"])


def _entry_to_node(e: KnowledgeEntry) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "tags": e.tags or [],
        "created_by": e.created_by,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        "size": max(1, len(e.content) // 200),
    }


# ── User-facing ────────────────────────────────────────────────────────────────

@router.get("/graph")
async def get_brain_graph(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Full Second Brain graph: knowledge nodes + both backlink and semantic edges."""
    from app.models.user import UserRole

    q = select(KnowledgeEntry)
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        q = q.where(KnowledgeEntry.user_id == str(user.id))

    entries = (await db.execute(q)).scalars().all()
    title_map = {e.title: e.id for e in entries}
    entry_ids = {e.id for e in entries}

    nodes = [_entry_to_node(e) for e in entries]

    edges = []
    seen = set()

    # Explicit [[backlinks]]
    import re
    BACKLINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
    for entry in entries:
        for target_title in BACKLINK_RE.findall(entry.content):
            target_id = title_map.get(target_title)
            if target_id and target_id != entry.id:
                key = tuple(sorted((entry.id, target_id)))
                if key not in seen:
                    edges.append({"source": entry.id, "target": target_id, "type": "backlink", "weight": 1.0})
                    seen.add(key)

    # Semantic links from brain_links
    if entry_ids:
        links = (await db.execute(
            select(BrainLink).where(
                BrainLink.source_id.in_(entry_ids),
                BrainLink.target_id.in_(entry_ids),
            )
        )).scalars().all()
        for link in links:
            key = tuple(sorted((link.source_id, link.target_id)))
            if key not in seen:
                edges.append({
                    "source": link.source_id,
                    "target": link.target_id,
                    "type": "semantic",
                    "weight": link.similarity or 0.0,
                })
                seen.add(key)

    return {"nodes": nodes, "edges": edges}


@router.get("/search")
async def search_brain(
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Semantic search across the user's full Second Brain."""
    from app.models.user import UserRole

    user_id = None if (hasattr(user, "role") and user.role == UserRole.ADMIN) else str(user.id)
    return await _brain_search(q=q, user_id=user_id, limit=limit, db=db)


@router.get("/related/{entry_id}")
async def get_related(
    entry_id: int,
    limit: int = Query(10, ge=1, le=50),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return semantically related knowledge entries for a given entry."""
    from app.models.user import UserRole

    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entry not found")
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        if entry.user_id != str(user.id):
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Access denied")

    links = (await db.execute(
        select(BrainLink).where(
            or_(BrainLink.source_id == entry_id, BrainLink.target_id == entry_id)
        ).order_by(BrainLink.similarity.desc()).limit(limit)
    )).scalars().all()

    related_ids = [
        (lnk.target_id if lnk.source_id == entry_id else lnk.source_id, lnk.similarity)
        for lnk in links
    ]

    result = []
    for rid, sim in related_ids:
        e = await db.get(KnowledgeEntry, rid)
        if e:
            node = _entry_to_node(e)
            node["similarity"] = sim
            result.append(node)

    return {"entry_id": entry_id, "related": result}


# ── Agent-facing ───────────────────────────────────────────────────────────────

async def _resolve_agent_user(auth: dict, db: AsyncSession) -> str | None:
    from app.models.agent import Agent
    agent = await db.get(Agent, auth["agent_id"])
    return agent.user_id if agent else None


@router.get("/agent/list")
async def agent_brain_list(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tag: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """List entries in the user's Second Brain (paginated)."""
    user_id = await _resolve_agent_user(auth, db)
    if not user_id:
        return {"entries": [], "total": 0}

    stmt = select(KnowledgeEntry).where(KnowledgeEntry.user_id == user_id)
    if tag:
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        stmt = stmt.where(cast(KnowledgeEntry.tags, JSONB).op("?")(tag))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(stmt.order_by(KnowledgeEntry.updated_at.desc()).limit(limit).offset(offset))).scalars().all()

    return {
        "entries": [
            {
                "id": e.id,
                "title": e.title,
                "tags": e.tags or [],
                "created_by": e.created_by,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/agent/get/{entry_id}")
async def agent_brain_get(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Fetch a single brain entry by id (must belong to agent's owner)."""
    user_id = await _resolve_agent_user(auth, db)
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry or entry.user_id != user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entry not found")

    # Bump access_count
    entry.access_count = (entry.access_count or 0) + 1
    await db.commit()

    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "tags": entry.tags or [],
        "created_by": entry.created_by,
        "updated_by": entry.updated_by,
        "access_count": entry.access_count,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


@router.put("/agent/update/{entry_id}")
async def agent_brain_update(
    entry_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Update an existing brain entry (must belong to agent's owner). Re-embeds + re-links."""
    from sqlalchemy import text as sa_text

    user_id = await _resolve_agent_user(auth, db)
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry or entry.user_id != user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entry not found")

    if "title" in body:
        title = (body["title"] or "").strip()
        if not title:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="title cannot be empty")
        entry.title = title
    if "content" in body:
        entry.content = body["content"] or ""
    if "tags" in body and isinstance(body["tags"], list):
        entry.tags = body["tags"]
    entry.updated_by = auth["agent_id"]

    await db.commit()
    await db.refresh(entry)

    # Re-embed + re-link
    try:
        from app.services.embedding_service import get_embedding_service
        from app.services.brain_linker import auto_link

        svc = get_embedding_service()
        if svc.enabled:
            emb = await svc.embed(f"{entry.title}: {entry.content}")
            if emb is not None:
                await db.execute(
                    sa_text("UPDATE knowledge_entries SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(emb), "id": entry.id},
                )
                await db.commit()
                if user_id:
                    await auto_link(entry.id, user_id, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"brain update embed/link failed: {e}")

    return {"id": entry.id, "title": entry.title, "tags": entry.tags or [], "updated_by": entry.updated_by}


@router.delete("/agent/delete/{entry_id}", status_code=200)
async def agent_brain_delete(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Delete a brain entry (must belong to agent's owner)."""
    from sqlalchemy import delete as sa_delete

    user_id = await _resolve_agent_user(auth, db)
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry or entry.user_id != user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entry not found")

    # Remove brain_links pointing to this entry
    await db.execute(sa_delete(BrainLink).where(
        or_(BrainLink.source_id == entry_id, BrainLink.target_id == entry_id)
    ))
    await db.delete(entry)
    await db.commit()

    return {"deleted": entry_id}


@router.get("/agent/related/{entry_id}")
async def agent_brain_related(
    entry_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Get semantically related entries for a brain node (must belong to agent's owner)."""
    user_id = await _resolve_agent_user(auth, db)
    entry = await db.get(KnowledgeEntry, entry_id)
    if not entry or entry.user_id != user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entry not found")

    links = (await db.execute(
        select(BrainLink).where(
            or_(BrainLink.source_id == entry_id, BrainLink.target_id == entry_id)
        ).order_by(BrainLink.similarity.desc()).limit(limit)
    )).scalars().all()

    related = []
    for lnk in links:
        rid = lnk.target_id if lnk.source_id == entry_id else lnk.source_id
        e = await db.get(KnowledgeEntry, rid)
        if e:
            related.append({
                "id": e.id,
                "title": e.title,
                "tags": e.tags or [],
                "similarity": round(float(lnk.similarity or 0), 4),
            })

    return {"entry_id": entry_id, "related": related, "total": len(related)}


@router.get("/agent/search")
async def agent_brain_search(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=50),
    include_memories: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Search across the user's full Second Brain (knowledge + optionally memories).

    Scoped to the agent's owner user — so all agents of that user share one brain.
    """
    from app.models.agent import Agent

    agent = await db.get(Agent, auth["agent_id"])
    user_id = agent.user_id if agent else None

    knowledge_results = await _brain_search(q=q, user_id=user_id, limit=limit, db=db)

    if not include_memories:
        return knowledge_results

    # Cross-agent memory search: find memories from all agents of this user
    memory_results = await _user_memory_search(q=q, user_id=user_id, limit=limit, db=db)
    return {
        **knowledge_results,
        "memories": memory_results,
    }


@router.post("/agent/contribute", status_code=201)
async def agent_contribute(
    body: dict,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Agent contributes a node to the user's Second Brain (knowledge entry).

    Upserts by title. Auto-links semantically after embedding.
    Body: {title, content, tags=[]}
    """
    from app.models.agent import Agent
    from sqlalchemy import text as sa_text

    agent_id = auth["agent_id"]
    agent = await db.get(Agent, agent_id)
    user_id = agent.user_id if agent else None

    title = body.get("title", "").strip()
    content = body.get("content", "")
    tags = body.get("tags", [])

    if not title:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="title is required")

    # Extract inline #tags
    import re
    inline_tags = list(set(re.findall(r"(?:^|\s)#([a-zA-Z0-9_-]+)", content, re.MULTILINE)))
    all_tags = list(set(tags + inline_tags))

    existing = (await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.title == title)
    )).scalar_one_or_none()

    if existing:
        existing.content = content
        existing.tags = all_tags
        existing.updated_by = agent_id
        entry = existing
    else:
        entry = KnowledgeEntry(
            title=title,
            content=content,
            tags=all_tags,
            created_by=agent_id,
            updated_by=agent_id,
            user_id=user_id,
        )
        db.add(entry)

    await db.commit()
    await db.refresh(entry)

    # Embed + auto-link
    try:
        from app.services.embedding_service import get_embedding_service
        from app.services.brain_linker import auto_link

        svc = get_embedding_service()
        if svc.enabled:
            emb = await svc.embed(f"{title}: {content}")
            if emb is not None:
                await db.execute(
                    sa_text("UPDATE knowledge_entries SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(emb), "id": entry.id},
                )
                await db.commit()
                if user_id:
                    await auto_link(entry.id, user_id, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"brain contribute embed/link failed: {e}")

    return {
        "id": entry.id,
        "title": entry.title,
        "tags": entry.tags,
        "created_by": entry.created_by,
        "updated_by": entry.updated_by,
    }


# ── Admin ─────────────────────────────────────────────────────────────────────

@router.post("/backfill", status_code=200)
async def backfill_brain_links(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Re-run auto_link for all knowledge entries that have embeddings.

    One-shot admin operation to seed brain_links for entries created before
    auto-linking was wired up. Safe to run multiple times (upsert logic).
    """
    from app.models.user import UserRole
    from app.services.brain_linker import auto_link

    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin only")

    from sqlalchemy import text as sa_text

    rows = (await db.execute(
        sa_text("SELECT id, user_id FROM knowledge_entries WHERE embedding IS NOT NULL AND user_id IS NOT NULL")
    )).all()

    total_links = 0
    processed = 0
    for entry_id, uid in rows:
        n = await auto_link(entry_id, uid, db)
        total_links += n
        processed += 1

    return {"processed": processed, "links_created": total_links}


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _brain_search(q: str, user_id: str | None, limit: int, db: AsyncSession) -> dict:
    """Semantic search within a user's knowledge entries, keyword fallback."""
    from sqlalchemy import text as sa_text, update

    if q and user_id:
        try:
            from app.services.embedding_service import get_embedding_service
            svc = get_embedding_service()
            if svc.enabled:
                qvec = await svc.embed(q)
                if qvec is not None:
                    rows = (await db.execute(
                        sa_text("""
                            SELECT id, title, content, tags, created_by, updated_by,
                                   access_count, created_at, updated_at,
                                   1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
                            FROM knowledge_entries
                            WHERE embedding IS NOT NULL AND user_id = :uid
                            ORDER BY embedding <=> CAST(:qvec AS vector)
                            LIMIT :limit
                        """),
                        {"qvec": str(qvec), "uid": user_id, "limit": limit},
                    )).mappings().all()

                    if rows:
                        ids = [r["id"] for r in rows]
                        await db.execute(
                            update(KnowledgeEntry)
                            .where(KnowledgeEntry.id.in_(ids))
                            .values(access_count=KnowledgeEntry.access_count + 1)
                        )
                        await db.commit()
                        return {
                            "entries": [
                                {
                                    "id": r["id"],
                                    "title": r["title"],
                                    "content": r["content"],
                                    "tags": r["tags"] or [],
                                    "similarity": round(float(r["similarity"]), 4),
                                    "created_by": r["created_by"],
                                }
                                for r in rows
                            ],
                            "total": len(rows),
                            "mode": "semantic",
                        }
        except Exception:
            pass

    # Keyword fallback
    stmt = select(KnowledgeEntry)
    if user_id:
        stmt = stmt.where(KnowledgeEntry.user_id == user_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(KnowledgeEntry.title.ilike(pattern), KnowledgeEntry.content.ilike(pattern))
        )
    stmt = stmt.order_by(KnowledgeEntry.updated_at.desc()).limit(limit)
    entries = (await db.execute(stmt)).scalars().all()

    return {
        "entries": [
            {
                "id": e.id,
                "title": e.title,
                "content": e.content,
                "tags": e.tags or [],
                "created_by": e.created_by,
            }
            for e in entries
        ],
        "total": len(entries),
        "mode": "keyword",
    }


async def _user_memory_search(q: str, user_id: str | None, limit: int, db: AsyncSession) -> list:
    """Search agent memories across all agents belonging to user_id."""
    if not user_id or not q:
        return []
    try:
        from sqlalchemy import text as sa_text
        from app.services.embedding_service import get_embedding_service

        svc = get_embedding_service()
        if svc.enabled:
            qvec = await svc.embed(q)
            if qvec is not None:
                rows = (await db.execute(
                    sa_text("""
                        SELECT am.id, am.agent_id, am.category, am.key, am.content,
                               am.importance, am.room,
                               1 - (am.embedding <=> CAST(:qvec AS vector)) AS similarity
                        FROM agent_memories am
                        JOIN agents a ON a.id = am.agent_id
                        WHERE a.user_id = :uid
                          AND am.embedding IS NOT NULL
                          AND am.superseded_by IS NULL
                        ORDER BY am.embedding <=> CAST(:qvec AS vector)
                        LIMIT :limit
                    """),
                    {"qvec": str(qvec), "uid": user_id, "limit": limit},
                )).mappings().all()
                return [
                    {
                        "id": r["id"],
                        "agent_id": r["agent_id"],
                        "category": r["category"],
                        "key": r["key"],
                        "content": r["content"],
                        "importance": r["importance"],
                        "room": r["room"],
                        "similarity": round(float(r["similarity"]), 4),
                    }
                    for r in rows
                ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"user memory search failed: {e}")
    return []
