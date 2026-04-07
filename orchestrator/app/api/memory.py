"""Agent long-term memory API - CRUD + search for persistent agent knowledge."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, or_, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, require_auth_or_agent, verify_agent_token
from app.models.memory import AgentMemory

router = APIRouter(prefix="/memory", tags=["memory"])


class MemorySave(BaseModel):
    agent_id: str
    category: str  # preference, contact, project, procedure, decision, fact, learning
    key: str
    content: str
    importance: int = 3


class MemoryUpdate(BaseModel):
    content: str | None = None
    importance: int | None = None
    category: str | None = None


class MemoryResponse(BaseModel):
    id: int
    agent_id: str
    category: str
    key: str
    content: str
    importance: int
    access_count: int
    created_at: str
    updated_at: str


# --- Agent-facing endpoints (called from inside containers) ---

@router.post("/save", response_model=MemoryResponse)
async def save_memory(
    body: MemorySave,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Save or update a memory entry. If agent_id+key exists, update it. Requires agent auth."""
    # Enforce agent can only write to its own memories
    if body.agent_id != auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Cannot write to another agent's memory")
    existing = await db.execute(
        select(AgentMemory)
        .where(AgentMemory.agent_id == body.agent_id)
        .where(AgentMemory.key == body.key)
    )
    memory = existing.scalar_one_or_none()

    if memory:
        memory.content = body.content
        memory.category = body.category
        memory.importance = body.importance
    else:
        memory = AgentMemory(
            agent_id=body.agent_id,
            category=body.category,
            key=body.key,
            content=body.content,
            importance=body.importance,
        )
        db.add(memory)

    await db.commit()
    await db.refresh(memory)

    # Generate embedding asynchronously (fire-and-forget, don't block the response)
    try:
        from app.services.embedding_service import get_embedding_service
        svc = get_embedding_service()
        if svc.enabled:
            # Embed the full content including context (key + content)
            text_to_embed = f"{body.key}: {body.content}"
            embedding = await svc.embed(text_to_embed)
            if embedding is not None:
                from sqlalchemy import text as sa_text
                await db.execute(
                    sa_text("UPDATE agent_memories SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(embedding), "id": memory.id},
                )
                await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to generate embedding: {e}")

    return _to_response(memory)


@router.get("/semantic-search")
async def semantic_search_memories(
    agent_id: str = Query(...),
    q: str = Query(..., min_length=1, description="Natural-language query"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Semantic vector search over an agent's memories.

    Falls back to keyword search if embeddings are not available.
    Requires agent auth — agent can only search its own memories.
    """
    if agent_id != auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Cannot search another agent's memory")

    from app.services.embedding_service import get_embedding_service
    from sqlalchemy import text as sa_text

    svc = get_embedding_service()
    if not svc.enabled:
        # Fall back to keyword search
        result = await db.execute(
            select(AgentMemory)
            .where(AgentMemory.agent_id == agent_id)
            .where(
                or_(
                    AgentMemory.content.ilike(f"%{q}%"),
                    AgentMemory.key.ilike(f"%{q}%"),
                )
            )
            .limit(limit)
        )
        memories = result.scalars().all()
        return {
            "memories": [_to_response(m) for m in memories],
            "mode": "keyword_fallback",
            "reason": "No OpenAI API key configured — set OPENAI_API_KEY for semantic search",
        }

    # Generate query embedding
    query_embedding = await svc.embed(q)
    if query_embedding is None:
        raise HTTPException(status_code=503, detail="Embedding generation failed")

    # Cosine-distance search using pgvector (<-> is cosine distance)
    # Lower distance = more similar
    sql = sa_text(
        """
        SELECT id, agent_id, category, key, content, importance, access_count,
               created_at, updated_at,
               1 - (embedding <=> CAST(:query_vec AS vector)) as similarity
        FROM agent_memories
        WHERE agent_id = :agent_id
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:query_vec AS vector)
        LIMIT :limit
        """
    )
    result = await db.execute(
        sql,
        {"query_vec": str(query_embedding), "agent_id": agent_id, "limit": limit},
    )
    rows = result.mappings().all()

    return {
        "memories": [
            {
                "id": r["id"],
                "agent_id": r["agent_id"],
                "category": r["category"],
                "key": r["key"],
                "content": r["content"],
                "importance": r["importance"],
                "access_count": r["access_count"],
                "similarity": round(float(r["similarity"]), 4),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ],
        "mode": "semantic",
        "query": q,
    }


@router.get("/search")
async def search_memories(
    agent_id: str,
    q: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Search agent memories by keyword and/or category. Requires agent auth."""
    # Enforce agent can only search its own memories
    if agent_id != auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Cannot search another agent's memory")
    query = select(AgentMemory).where(AgentMemory.agent_id == agent_id)

    if category:
        query = query.where(AgentMemory.category == category)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                AgentMemory.key.ilike(pattern),
                AgentMemory.content.ilike(pattern),
            )
        )

    query = query.order_by(AgentMemory.importance.desc(), AgentMemory.updated_at.desc()).limit(limit)
    result = await db.execute(query)
    memories = result.scalars().all()

    # Bump access count for retrieved memories
    if memories:
        ids = [m.id for m in memories]
        await db.execute(
            update(AgentMemory)
            .where(AgentMemory.id.in_(ids))
            .values(access_count=AgentMemory.access_count + 1)
        )
        await db.commit()

    return {"memories": [_to_response(m) for m in memories], "total": len(memories)}


# --- UI-facing endpoints ---

@router.get("/preload/{agent_id}")
async def preload_critical_memories(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the agent's most critical memories for prompt injection.

    Returns memories that the agent should ALWAYS know:
    - importance >= 4 (user corrections, key decisions, credentials)
    - categories: credentials, preference, procedure
    - recent learnings (last 10)

    Open endpoint (no auth) — agents fetch their own preload on every task start.
    """
    # High-importance memories (importance >= 4)
    high_imp_result = await db.execute(
        select(AgentMemory)
        .where(AgentMemory.agent_id == agent_id)
        .where(AgentMemory.importance >= 4)
        .order_by(AgentMemory.importance.desc(), AgentMemory.updated_at.desc())
        .limit(20)
    )
    high_imp = list(high_imp_result.scalars().all())

    # All credentials/keys (always relevant)
    creds_result = await db.execute(
        select(AgentMemory)
        .where(AgentMemory.agent_id == agent_id)
        .where(AgentMemory.category.in_(["credentials", "api_key", "secret", "auth"]))
        .order_by(AgentMemory.updated_at.desc())
        .limit(30)
    )
    creds = list(creds_result.scalars().all())

    # Recent learnings (last 10)
    learnings_result = await db.execute(
        select(AgentMemory)
        .where(AgentMemory.agent_id == agent_id)
        .where(AgentMemory.category == "learning")
        .order_by(AgentMemory.updated_at.desc())
        .limit(10)
    )
    learnings = list(learnings_result.scalars().all())

    # Deduplicate by ID
    seen_ids = set()
    def _dedupe(items):
        out = []
        for m in items:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                out.append({
                    "key": m.key,
                    "category": m.category,
                    "content": m.content,
                    "importance": m.importance,
                })
        return out

    return {
        "critical": _dedupe(high_imp),
        "credentials": _dedupe(creds),
        "recent_learnings": _dedupe(learnings),
    }


@router.get("/agents/{agent_id}")
async def list_agent_memories(
    agent_id: str,
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """List all memories for an agent (for the frontend Memory tab + agent API)."""
    query = select(AgentMemory).where(AgentMemory.agent_id == agent_id)
    if category:
        query = query.where(AgentMemory.category == category)
    query = query.order_by(AgentMemory.importance.desc(), AgentMemory.updated_at.desc()).limit(limit)
    result = await db.execute(query)
    memories = result.scalars().all()

    # Group by category for UI
    categories: dict[str, int] = {}
    for m in memories:
        categories[m.category] = categories.get(m.category, 0) + 1

    return {
        "memories": [_to_response(m) for m in memories],
        "total": len(memories),
        "categories": categories,
    }


@router.put("/{memory_id}")
async def update_memory(
    memory_id: int,
    body: MemoryUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update a memory entry (from UI)."""
    result = await db.execute(select(AgentMemory).where(AgentMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    if body.content is not None:
        memory.content = body.content
    if body.importance is not None:
        memory.importance = body.importance
    if body.category is not None:
        memory.category = body.category

    await db.commit()
    await db.refresh(memory)
    return _to_response(memory)


@router.delete("/{memory_id}")
async def delete_memory(memory_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Delete a memory entry."""
    result = await db.execute(select(AgentMemory).where(AgentMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.delete(memory)
    await db.commit()
    return {"deleted": memory_id}


def _to_response(m: AgentMemory) -> dict:
    return {
        "id": m.id,
        "agent_id": m.agent_id,
        "category": m.category,
        "key": m.key,
        "content": m.content,
        "importance": m.importance,
        "access_count": m.access_count,
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "updated_at": m.updated_at.isoformat() if m.updated_at else "",
    }
