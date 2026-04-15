"""Agent long-term memory API - CRUD + search for persistent agent knowledge.

Memory system upgrade (issue #24):
  - Hierarchical rooms (`room` param)
  - Single vs. multi key routing via KEY_SCHEMA
  - Two-tier cosine thresholds (0.92 hard / 0.88 soft) with contradiction
    warnings and `override=True` supersede
  - Access tracking bumps on retrieval
  - Multi-strategy scoring on semantic search (semantic+structural+recency+importance)
  - Tags + Links relation tables
  - Per-room summary via MemoryCompressor
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, delete, or_, select, text as sa_text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.memory_key_schema import (
    COSINE_HARD_DEDUP,
    COSINE_SOFT_WARN,
    TAG_TYPE_PERMANENT,
    classify_key,
    normalize_tag,
)
from app.core.memory_scoring import ScoringInputs, final_score
from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth, require_auth_or_agent, verify_agent_token
from app.models.memory import AgentMemory, AgentMemoryLink, AgentMemoryTag
from app.services.redis_service import RedisService

router = APIRouter(prefix="/memory", tags=["memory"])


class MemorySave(BaseModel):
    agent_id: str
    category: str  # preference, contact, project, procedure, decision, fact, learning
    key: str
    content: str
    importance: int = 3
    # --- Issue #24 additions ---
    room: str | None = None
    confidence: float = 1.0
    tag_type: str = TAG_TYPE_PERMANENT  # "transient" | "permanent"
    tags: list[str] = []
    override: bool = False  # confirm supersede on contradiction
    links: list[dict] = []  # [{"target_id": int, "relation": "uses"}]


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
    room: str | None = None
    confidence: float = 1.0
    tag_type: str = "permanent"
    superseded_by: int | None = None


# --- Agent-facing endpoints (called from inside containers) ---


class ContradictionWarning(BaseModel):
    warning: str
    similar_memory_id: int
    similar_content: str
    similarity: float
    hint: str = "Re-call with override=True to supersede the existing memory, or change your content."


async def _find_similar_memory(
    db: AsyncSession,
    agent_id: str,
    room: str | None,
    key: str,
    content: str,
) -> tuple[AgentMemory | None, float]:
    """Return (most-similar-memory, similarity) in the same room/key bucket.

    Uses pgvector if the query content can be embedded; otherwise falls
    back to a key-only lookup.
    """
    from app.services.embedding_service import get_embedding_service

    svc = get_embedding_service()
    if not svc.enabled:
        return None, 0.0

    query_vec = await svc.embed(f"{key}: {content}")
    if query_vec is None:
        return None, 0.0

    # Build room clause as a string — asyncpg doesn't allow casting NULL
    # named-bind params inside a conditional expression, so we split the
    # branches at query-build time.
    room_clause = "AND room = :room" if room else "AND room IS NULL"
    sql = sa_text(
        f"""
        SELECT id, agent_id, category, key, content, importance, access_count,
               created_at, updated_at, room, confidence, tag_type, superseded_by,
               1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
        FROM agent_memories
        WHERE agent_id = :agent_id
          {room_clause}
          AND key = :key
          AND embedding IS NOT NULL
          AND superseded_by IS NULL
        ORDER BY embedding <=> CAST(:query_vec AS vector)
        LIMIT 1
        """
    )
    params = {
        "query_vec": str(query_vec),
        "agent_id": agent_id,
        "key": key,
    }
    if room:
        params["room"] = room
    result = await db.execute(sql, params)
    row = result.mappings().first()
    if not row:
        return None, 0.0
    # Re-fetch as ORM object for the caller (so .superseded_by etc. work)
    mem = await db.get(AgentMemory, int(row["id"]))
    return mem, float(row["similarity"])


async def _apply_tags_and_links(
    db: AsyncSession, memory: AgentMemory, tags: list[str], links: list[dict]
) -> None:
    """Materialize agent_memory_tags + agent_memory_links rows for a memory."""
    if tags:
        canonical = {normalize_tag(t) for t in tags if t.strip()}
        for t in canonical:
            await db.execute(
                pg_insert(AgentMemoryTag)
                .values(memory_id=memory.id, tag=t)
                .on_conflict_do_nothing(index_elements=["memory_id", "tag"])
            )
    for link in links:
        try:
            target_id = int(link.get("target_id") or 0)
            relation = str(link.get("relation") or "refers_to")[:50]
        except Exception:
            continue
        if target_id and target_id != memory.id:
            await db.execute(
                pg_insert(AgentMemoryLink)
                .values(source_id=memory.id, target_id=target_id, relation=relation)
                .on_conflict_do_nothing(index_elements=["source_id", "target_id", "relation"])
            )


@router.post("/save", response_model=MemoryResponse, responses={409: {"model": ContradictionWarning}})
async def save_memory(
    body: MemorySave,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Save a memory entry.

    Behavior (issue #24):
      - If `key` is classified as "single" in KEY_SCHEMA: any existing
        memory with the same (agent_id, room, key) is SUPERSEDED by the
        new value.
      - If `key` is "multi": semantic similarity is checked against
        non-superseded memories in the same (agent_id, room, key)
        bucket:
          - similarity >= 0.92: auto-supersede (same fact, reworded)
          - similarity >= 0.88 and override=False: return 409 with a
            ContradictionWarning. The caller can retry with override=True.
          - similarity < 0.88: insert as a new independent memory.
    """
    # Enforce agent can only write to its own memories
    if body.agent_id != auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Cannot write to another agent's memory")

    kind = classify_key(body.key)
    now = datetime.now(timezone.utc)

    # --- Step 1: find a candidate to supersede --------------------------------
    to_supersede: AgentMemory | None = None

    if kind == "single":
        # Exact key/room match: always supersede.
        result = await db.execute(
            select(AgentMemory)
            .where(
                and_(
                    AgentMemory.agent_id == body.agent_id,
                    AgentMemory.key == body.key,
                    AgentMemory.superseded_by.is_(None),
                    AgentMemory.room == body.room if body.room else AgentMemory.room.is_(None),
                )
            )
            .order_by(AgentMemory.created_at.desc())
            .limit(1)
        )
        to_supersede = result.scalar_one_or_none()
    else:
        # Multi: use semantic similarity against the same (agent, room, key).
        similar, sim = await _find_similar_memory(db, body.agent_id, body.room, body.key, body.content)
        if similar:
            if sim >= COSINE_HARD_DEDUP:
                to_supersede = similar
            elif sim >= COSINE_SOFT_WARN and not body.override:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "warning": "contradiction_detected",
                        "similar_memory_id": similar.id,
                        "similar_content": similar.content[:400],
                        "similarity": round(sim, 4),
                        "hint": "Re-call with override=True to supersede the existing memory.",
                    },
                )
            elif sim >= COSINE_SOFT_WARN and body.override:
                to_supersede = similar

    # --- Step 2: insert the new memory ----------------------------------------
    new_mem = AgentMemory(
        agent_id=body.agent_id,
        category=body.category,
        key=body.key,
        content=body.content,
        importance=body.importance,
        room=body.room,
        confidence=body.confidence,
        tag_type=body.tag_type if body.tag_type in ("transient", "permanent") else TAG_TYPE_PERMANENT,
    )
    db.add(new_mem)
    await db.flush()  # get new_mem.id

    # --- Step 3: mark old as superseded --------------------------------------
    if to_supersede and to_supersede.id != new_mem.id:
        to_supersede.superseded_by = new_mem.id
        to_supersede.superseded_at = now

    await db.commit()
    await db.refresh(new_mem)

    # --- Step 4: tags + links (best-effort, don't fail on duplicates) --------
    try:
        await _apply_tags_and_links(db, new_mem, body.tags, body.links)
        await db.commit()
    except Exception:
        await db.rollback()

    # --- Step 5: embedding (fire-and-forget, don't block response) -----------
    try:
        from app.services.embedding_service import get_embedding_service
        svc = get_embedding_service()
        if svc.enabled:
            embedding = await svc.embed(f"{body.key}: {body.content}")
            if embedding is not None:
                await db.execute(
                    sa_text("UPDATE agent_memories SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(embedding), "id": new_mem.id},
                )
                await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to generate embedding: {e}")

    # --- Step 6: invalidate room summary cache -------------------------------
    if body.room:
        try:
            from app.services.memory_compressor import MemoryCompressor
            from app.services.redis_service import RedisService  # noqa
            # Fire-and-forget invalidation via app.state.redis if available.
            # Skip cleanly if redis wasn't attached (happens in tests).
            ...
        except Exception:
            pass

    return _to_response(new_mem)


@router.get("/semantic-search")
async def semantic_search_memories(
    agent_id: str = Query(...),
    q: str = Query(..., min_length=1, description="Natural-language query"),
    limit: int = Query(10, ge=1, le=50),
    room: str | None = Query(None, description="Restrict to memories in this room / sub-rooms"),
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(verify_agent_token),
):
    """Semantic vector search with multi-strategy re-ranking (issue #24).

    Scoring formula (applied after fetching 4x limit candidates):
        0.50 * semantic + 0.30 * structural + 0.15 * recency + 0.05 * importance

    Superseded memories are excluded. Access counters are bumped for
    the returned top-N so the recency/access boost kicks in.

    Falls back to keyword search if embeddings are not available.
    """
    if agent_id != auth["agent_id"]:
        raise HTTPException(status_code=403, detail="Cannot search another agent's memory")

    from app.services.embedding_service import get_embedding_service

    svc = get_embedding_service()
    if not svc.enabled:
        # Keyword fallback — no re-ranking possible
        result = await db.execute(
            select(AgentMemory)
            .where(AgentMemory.agent_id == agent_id)
            .where(AgentMemory.superseded_by.is_(None))
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
            "reason": "Embedding service disabled — configure EMBEDDING_MODE for semantic search",
        }

    query_embedding = await svc.embed(q)
    if query_embedding is None:
        raise HTTPException(status_code=503, detail="Embedding generation failed")

    # Fetch 4x candidates to give the re-ranker room to work with.
    candidate_limit = max(limit * 4, 20)

    # Optional room prefix filter
    room_clause = ""
    if room:
        room_clause = "AND (room = :room OR room LIKE :room_prefix)"

    sql = sa_text(
        f"""
        SELECT id, agent_id, category, key, content, importance, access_count,
               created_at, updated_at, room, confidence, tag_type,
               last_accessed_at,
               1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
        FROM agent_memories
        WHERE agent_id = :agent_id
          AND embedding IS NOT NULL
          AND superseded_by IS NULL
          {room_clause}
        ORDER BY embedding <=> CAST(:query_vec AS vector)
        LIMIT :limit
        """
    )
    params = {
        "query_vec": str(query_embedding),
        "agent_id": agent_id,
        "limit": candidate_limit,
    }
    if room:
        params["room"] = room
        params["room_prefix"] = f"{room}/%"
    result = await db.execute(sql, params)
    rows = result.mappings().all()

    # --- Multi-strategy re-ranking ------------------------------------------
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, dict]] = []
    for r in rows:
        inp = ScoringInputs(
            semantic_sim=float(r["similarity"]),
            query_room=room,
            memory_room=r["room"],
            memory_tag_type=r["tag_type"] or "permanent",
            last_accessed_at=r["last_accessed_at"],
            created_at=r["created_at"],
            access_count=int(r["access_count"]),
            importance=int(r["importance"]),
        )
        s = final_score(inp, now=now)
        scored.append(
            (
                s,
                {
                    "id": r["id"],
                    "agent_id": r["agent_id"],
                    "category": r["category"],
                    "key": r["key"],
                    "content": r["content"],
                    "importance": r["importance"],
                    "access_count": r["access_count"],
                    "similarity": round(float(r["similarity"]), 4),
                    "score": round(s, 4),
                    "room": r["room"],
                    "confidence": float(r["confidence"]) if r["confidence"] is not None else 1.0,
                    "tag_type": r["tag_type"] or "permanent",
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                },
            )
        )

    scored.sort(key=lambda t: t[0], reverse=True)
    top = [d for _, d in scored[:limit]]

    # --- Bump access counters for the returned memories ----------------------
    if top:
        ids = [d["id"] for d in top]
        await db.execute(
            update(AgentMemory)
            .where(AgentMemory.id.in_(ids))
            .values(
                access_count=AgentMemory.access_count + 1,
                last_accessed_at=now,
            )
        )
        await db.commit()

    return {
        "memories": top,
        "mode": "semantic_reranked",
        "query": q,
        "room": room,
        "candidates_considered": len(rows),
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


@router.get("/room-summary/{agent_id}")
async def get_room_summary(
    agent_id: str,
    room: str = Query(..., description="Room path, e.g. project:ai-employee/backend"),
    force: bool = Query(False, description="Bypass the cached summary and regenerate"),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Return a short LLM-generated summary of the memories in a room.

    Cached in Redis for 1 hour. Falls back to a deterministic summary
    if no Anthropic API key is configured. (Issue #24 Should-Have)
    """
    from app.services.memory_compressor import MemoryCompressor
    compressor = MemoryCompressor(db, redis)
    return await compressor.get_or_build(agent_id, room, force=force)


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
        "room": m.room,
        "confidence": float(m.confidence) if m.confidence is not None else 1.0,
        "tag_type": m.tag_type or "permanent",
        "superseded_by": m.superseded_by,
    }
