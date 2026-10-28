"""Hybrid retrieval over the Second Brain vault chunks.

Fuses two signals with Reciprocal Rank Fusion:
  * semantic  — pgvector cosine distance over chunk embeddings, and
  * keyword    — Postgres full-text (tsvector/``websearch_to_tsquery`` + ``ts_rank_cd``),
which upgrades the old substring grep to real BM25-style ranking.

Graceful degradation (Pi-friendly):
  * embeddings disabled/unavailable  -> keyword (FTS) only,
  * vault not indexed yet (no chunks) -> pure grep via :func:`app.core.vault.search`.

Returns the same shape as ``vault.search`` — ``[{path, score, snippets}]`` — so
the MCP ``brain_search`` handler formats results unchanged.
"""
from __future__ import annotations

import logging

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import vault
from app.core.rrf import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# How many candidates to pull from each signal before fusion.
_CANDIDATES = 30
# Trust semantic a bit more than keyword when both fire (Advanced-RAG default).
_WEIGHTS = (1.3, 1.0)


async def _has_chunks(db: AsyncSession, brain_label: str) -> bool:
    row = (
        await db.execute(
            sa_text("SELECT 1 FROM vault_chunks WHERE brain_label = :b LIMIT 1"),
            {"b": brain_label},
        )
    ).first()
    return row is not None


async def _semantic_ids(
    db: AsyncSession, brain_label: str, query: str
) -> list[int]:
    try:
        from app.services.embedding_service import get_embedding_service

        qvec = await get_embedding_service().embed(query)
    except Exception as e:
        logger.warning("[VaultSearch] embed failed, semantic skipped: %s", e)
        qvec = None
    if not qvec:
        return []
    rows = (
        await db.execute(
            sa_text(
                "SELECT id FROM vault_chunks "
                "WHERE brain_label = :b AND embedding IS NOT NULL "
                "ORDER BY embedding <=> CAST(:qv AS vector) ASC "
                "LIMIT :lim"
            ),
            {"b": brain_label, "qv": str(qvec), "lim": _CANDIDATES},
        )
    ).scalars().all()
    return list(rows)


async def _keyword_ids(
    db: AsyncSession, brain_label: str, query: str
) -> list[int]:
    rows = (
        await db.execute(
            sa_text(
                "SELECT id FROM vault_chunks "
                "WHERE brain_label = :b "
                "AND ts @@ websearch_to_tsquery('simple', :q) "
                "ORDER BY ts_rank_cd(ts, websearch_to_tsquery('simple', :q)) DESC "
                "LIMIT :lim"
            ),
            {"b": brain_label, "q": query, "lim": _CANDIDATES},
        )
    ).scalars().all()
    return list(rows)


def _snippets(content: str, query: str, max_lines: int = 3) -> list[str]:
    terms = [t for t in query.lower().split() if t]
    out: list[str] = []
    for line in content.splitlines():
        low = line.lower()
        if any(t in low for t in terms):
            s = line.strip()
            if s:
                out.append(s[:240])
            if len(out) >= max_lines:
                break
    if not out:  # semantic-only hit — show the passage head
        head = content.strip().splitlines()
        out = [ln.strip()[:240] for ln in head[:2] if ln.strip()]
    return out


async def hybrid_search(
    db: AsyncSession, brain_label: str, host_path: str, query: str, limit: int = 10
) -> list[dict]:
    """Hybrid semantic+keyword search over a vault, grep as last-resort fallback.

    Thin wrapper over :func:`hybrid_search_with_mode` for the callers that only
    want the hits (external MCP endpoint, voice session).
    """
    _mode, results = await hybrid_search_with_mode(db, brain_label, host_path, query, limit)
    return results


async def hybrid_search_with_mode(
    db: AsyncSession, brain_label: str, host_path: str, query: str, limit: int = 10
) -> tuple[str, list[dict]]:
    """Like :func:`hybrid_search`, but also reports WHICH path produced the hits.

    Returns ``("hybrid"|"grep", results)``. The mode is not cosmetic: a vault that
    HAS chunks and still answers from grep is a drift signal — ``_has_chunks``
    treats a single chunk row as "indexed", so a partially stale index never
    degrades visibly on its own. Every grep answer despite existing chunks is
    therefore logged at warning level (spec section 6), and the mode travels out
    to the agent endpoint so the drift is readable without digging in the log.
    """
    query = (query or "").strip()
    if not query:
        return "hybrid", []

    indexed = await _has_chunks(db, brain_label)
    if not indexed:
        # Not indexed yet — keep the vault searchable with plain grep.
        return "grep", vault.search(host_path, query, limit)

    def _degraded(reason: str) -> tuple[str, list[dict]]:
        """Grep fallback on an INDEXED vault — warn only when grep beats the index.

        Both empty means the query genuinely matches nothing; that is an honest
        answer, not drift, and warning on it would bury the real signal. Grep
        finding files the index did not know about is the drift case: those files
        exist on disk and are missing from ``vault_chunks``.
        """
        hits = vault.search(host_path, query, limit)
        if hits:
            logger.warning(
                "[VaultSearch] brain=%s has chunks but grep found %d file(s) the "
                "index did not (%s) — index is stale, a re-index run is due",
                vault.safe_log(brain_label), len(hits), reason,
            )
        return "grep", hits

    semantic = await _semantic_ids(db, brain_label, query)
    keyword = await _keyword_ids(db, brain_label, query)

    if not semantic and not keyword:
        return _degraded("neither signal returned candidates")

    fused = reciprocal_rank_fusion([semantic, keyword], weights=list(_WEIGHTS))
    fused_ids = [cid for cid, _ in fused]
    if not fused_ids:
        return _degraded("fusion produced no ids")

    rows = (
        await db.execute(
            sa_text(
                "SELECT id, path, heading, content FROM vault_chunks WHERE id = ANY(:ids)"
            ),
            {"ids": fused_ids},
        )
    ).all()
    by_id = {r.id: r for r in rows}

    # Fuse chunk ranking up to file level: a file's score is its best chunk's
    # fused score; keep the best passage as the snippet source.
    file_rank: dict[str, float] = {}
    file_best: dict[str, object] = {}
    for cid, score in fused:
        r = by_id.get(cid)
        if r is None:
            continue
        if r.path not in file_rank or score > file_rank[r.path]:
            file_rank[r.path] = score
            file_best[r.path] = r

    ordered = sorted(file_rank.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    results: list[dict] = []
    for path, score in ordered:
        r = file_best[path]
        heading = f"{r.heading}: " if r.heading else ""
        snips = _snippets(r.content, query)
        if heading and snips:
            snips[0] = heading + snips[0]
        results.append({"path": path, "score": round(score, 6), "snippets": snips})
    return "hybrid", results
