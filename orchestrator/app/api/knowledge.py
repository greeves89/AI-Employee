"""Shared Knowledge Base API - Obsidian-style documents with backlinks, tags, and graph view."""

import re
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.knowledge import KnowledgeEntry

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Regex to extract [[backlinks]] from markdown content
BACKLINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([a-zA-Z0-9_-]+)", re.MULTILINE)


def _extract_backlinks(content: str) -> list[str]:
    """Extract [[backlink]] references from markdown content."""
    return list(set(BACKLINK_RE.findall(content)))


def _extract_tags(content: str) -> list[str]:
    """Extract #tags from markdown content (in addition to explicit tags)."""
    return list(set(TAG_RE.findall(content)))


def _to_response(entry: KnowledgeEntry, backlinks: list[str] | None = None) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "tags": entry.tags or [],
        "backlinks": backlinks if backlinks is not None else _extract_backlinks(entry.content),
        "created_by": entry.created_by,
        "updated_by": entry.updated_by,
        "access_count": entry.access_count,
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else "",
    }


# --- Pydantic models ---

class KnowledgeCreate(BaseModel):
    title: str
    content: str = ""
    tags: list[str] = []


class KnowledgeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None


# --- UI-facing endpoints (require user auth) ---

@router.get("/entries")
async def list_entries(
    q: str | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all knowledge entries with optional search and tag filter."""
    query = select(KnowledgeEntry)

    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                KnowledgeEntry.title.ilike(pattern),
                KnowledgeEntry.content.ilike(pattern),
            )
        )
    if tag:
        # JSON array contains check (PostgreSQL)
        query = query.where(KnowledgeEntry.tags.contains([tag]))

    total_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_q)).scalar() or 0

    query = query.order_by(KnowledgeEntry.updated_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    return {
        "entries": [_to_response(e) for e in entries],
        "total": total,
    }


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a single knowledge entry by ID."""
    result = await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    # Bump access count
    entry.access_count += 1
    await db.commit()

    # Find incoming backlinks (other entries that link to this one)
    incoming_q = select(KnowledgeEntry).where(
        KnowledgeEntry.content.contains(f"[[{entry.title}]]")
    )
    incoming = (await db.execute(incoming_q)).scalars().all()
    incoming_titles = [e.title for e in incoming if e.id != entry.id]

    resp = _to_response(entry)
    resp["incoming_backlinks"] = incoming_titles
    return resp


@router.post("/entries", status_code=201)
async def create_entry(
    body: KnowledgeCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new knowledge entry."""
    # Check for duplicate title
    existing = await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.title == body.title)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Entry '{body.title}' already exists")

    # Auto-extract tags from content if not provided
    all_tags = list(set(body.tags + _extract_tags(body.content)))

    owner_id = str(user.id) if hasattr(user, "id") and str(user.id) != "__anonymous__" else None
    entry = KnowledgeEntry(
        title=body.title,
        content=body.content,
        tags=all_tags,
        created_by="user",
        updated_by="user",
        user_id=owner_id,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _to_response(entry)


@router.put("/entries/{entry_id}")
async def update_entry(
    entry_id: int,
    body: KnowledgeUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a knowledge entry."""
    from app.models.user import UserRole
    result = await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        if entry.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")

    if body.title is not None:
        entry.title = body.title
    if body.content is not None:
        entry.content = body.content
        # Auto-extract tags from new content
        content_tags = _extract_tags(body.content)
        if body.tags is not None:
            entry.tags = list(set(body.tags + content_tags))
        else:
            existing_tags = entry.tags or []
            entry.tags = list(set(existing_tags + content_tags))
    elif body.tags is not None:
        entry.tags = body.tags

    entry.updated_by = "user"
    await db.commit()
    await db.refresh(entry)
    return _to_response(entry)


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a knowledge entry."""
    from app.models.user import UserRole
    result = await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        if entry.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")
    await db.delete(entry)
    await db.commit()
    return {"deleted": entry_id}


@router.get("/tags")
async def list_tags(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all unique tags with their usage count."""
    result = await db.execute(select(KnowledgeEntry.tags))
    all_tags: dict[str, int] = {}
    for (tags,) in result:
        if tags:
            for tag in tags:
                all_tags[tag] = all_tags.get(tag, 0) + 1

    sorted_tags = sorted(all_tags.items(), key=lambda x: x[1], reverse=True)
    return {"tags": [{"name": t, "count": c} for t, c in sorted_tags]}


@router.get("/graph")
async def get_graph(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get graph data for visualization: nodes (entries) and edges (backlinks)."""
    result = await db.execute(select(KnowledgeEntry))
    entries = result.scalars().all()

    # Build title -> id map
    title_map = {e.title: e.id for e in entries}

    nodes = []
    edges = []

    for entry in entries:
        nodes.append({
            "id": entry.id,
            "title": entry.title,
            "tags": entry.tags or [],
            "size": max(1, len(entry.content) // 200),  # Size by content length
        })

        # Extract outgoing backlinks and create edges
        backlinks = _extract_backlinks(entry.content)
        for target_title in backlinks:
            target_id = title_map.get(target_title)
            if target_id and target_id != entry.id:
                edges.append({
                    "source": entry.id,
                    "target": target_id,
                })

    return {"nodes": nodes, "edges": edges}


# --- Agent-facing endpoints (called from containers via MCP) ---

@router.post("/agent/write")
async def agent_write(
    body: KnowledgeCreate,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Create or update a knowledge entry (agent-facing). Upsert by title."""
    agent_id = auth["agent_id"]

    existing = await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.title == body.title)
    )
    entry = existing.scalar_one_or_none()

    all_tags = list(set(body.tags + _extract_tags(body.content)))

    if entry:
        entry.content = body.content
        entry.tags = all_tags
        entry.updated_by = agent_id
    else:
        entry = KnowledgeEntry(
            title=body.title,
            content=body.content,
            tags=all_tags,
            created_by=agent_id,
            updated_by=agent_id,
        )
        db.add(entry)

    await db.commit()
    await db.refresh(entry)

    # Auto-embed for semantic search
    try:
        from app.services.embedding_service import get_embedding_service
        from sqlalchemy import text as sa_text
        svc = get_embedding_service()
        if svc.enabled:
            text_to_embed = f"{body.title}: {body.content}"
            emb = await svc.embed(text_to_embed)
            if emb is not None:
                await db.execute(
                    sa_text("UPDATE knowledge_entries SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(emb), "id": entry.id},
                )
                await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to embed knowledge entry: {e}")

    return _to_response(entry)


@router.get("/agent/search")
async def agent_search(
    q: str = Query(""),
    tag: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Search knowledge base (agent-facing).

    If OpenAI embeddings are available AND no tag filter is set, uses semantic
    vector search. Otherwise falls back to keyword matching.
    """
    # Try semantic search first
    if q and not tag:
        try:
            from app.services.embedding_service import get_embedding_service
            from sqlalchemy import text as sa_text

            svc = get_embedding_service()
            if svc.enabled:
                qvec = await svc.embed(q)
                if qvec is not None:
                    rows = (await db.execute(
                        sa_text(
                            """
                            SELECT id, title, content, tags, created_by, updated_by,
                                   access_count, created_at, updated_at,
                                   1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
                            FROM knowledge_entries
                            WHERE embedding IS NOT NULL
                            ORDER BY embedding <=> CAST(:qvec AS vector)
                            LIMIT :limit
                            """
                        ),
                        {"qvec": str(qvec), "limit": limit},
                    )).mappings().all()

                    if rows:
                        # Bump access count
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
                                    "created_by": r["created_by"],
                                    "updated_by": r["updated_by"],
                                    "access_count": r["access_count"] + 1,
                                    "similarity": round(float(r["similarity"]), 4),
                                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                                }
                                for r in rows
                            ],
                            "total": len(rows),
                            "mode": "semantic",
                        }
        except Exception:
            pass  # Fall through to keyword search

    # Keyword search fallback
    query = select(KnowledgeEntry)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                KnowledgeEntry.title.ilike(pattern),
                KnowledgeEntry.content.ilike(pattern),
            )
        )
    if tag:
        query = query.where(KnowledgeEntry.tags.contains([tag]))

    query = query.order_by(KnowledgeEntry.updated_at.desc()).limit(limit)
    result = await db.execute(query)
    entries = result.scalars().all()

    if entries:
        ids = [e.id for e in entries]
        await db.execute(
            update(KnowledgeEntry)
            .where(KnowledgeEntry.id.in_(ids))
            .values(access_count=KnowledgeEntry.access_count + 1)
        )
        await db.commit()

    return {
        "entries": [_to_response(e) for e in entries],
        "total": len(entries),
        "mode": "keyword",
    }


@router.get("/agent/read/{title:path}")
async def agent_read(
    title: str,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Read a specific knowledge entry by title (agent-facing)."""
    result = await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.title == title)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail=f"Knowledge entry '{title}' not found")

    entry.access_count += 1
    await db.commit()
    return _to_response(entry)
