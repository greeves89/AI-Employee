"""Vault indexer — chunk + embed Second Brain Markdown files into ``vault_chunks``.

Walks a vault, splits each Markdown/text file into passages
(:func:`app.core.chunking.chunk_markdown`), embeds them in a batch, and upserts
the rows keyed on a per-file content hash so unchanged files are skipped on
re-index. This is the "indexing" phase of the RAG pipeline; retrieval lives in
:mod:`app.services.vault_search`.

Embedding is best-effort: if the embedding service is disabled (e.g. on the Pi),
chunks are still written *without* a vector, so keyword/tsvector search keeps
working. Semantic search lights up automatically once embeddings are available
and a re-index runs.
"""
from __future__ import annotations

import hashlib
import logging
import os

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import vault
from app.core.chunking import chunk_markdown

logger = logging.getLogger(__name__)

_SEARCHABLE_SUFFIXES = (".md", ".markdown", ".txt")


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _iter_files(base: str):
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if f.lower().endswith(_SEARCHABLE_SUFFIXES):
                full = os.path.join(root, f)
                yield full, os.path.relpath(full, base)


async def _embed_chunks(texts: list[str]) -> list[list[float] | None]:
    """Embed chunk texts, tolerating a disabled/failed embedding service."""
    if not texts:
        return []
    try:
        from app.services.embedding_service import get_embedding_service

        svc = get_embedding_service()
        return await svc.embed_batch(texts)
    except Exception as e:  # never let embedding break indexing
        logger.warning("[VaultIndexer] embedding unavailable, indexing without vectors: %s", e)
        return [None] * len(texts)


async def index_file(
    db: AsyncSession, brain_label: str, host_path: str, rel_path: str,
    nachfuellen: dict | None = None,
) -> int:
    """(Re)index a single vault file. Returns the number of chunks written.

    ``nachfuellen`` ist der Zustand eines ganzen Laufs (``reindex_vault``): Fehlt der
    Embedding-Dienst, fragt nicht jede weitere Datei erneut nach.
    """
    try:
        content = vault.read_file(host_path, rel_path)
    except (FileNotFoundError, ValueError):
        await remove_file(db, brain_label, rel_path)
        return 0
    # Postgres nimmt kein NUL-Byte in Text. Eine einzige solche Datei brach vorher
    # die gemeinsame Sitzung ab — und damit den Rest des Laufs.
    content = content.replace("\x00", "")

    fhash = _file_hash(content)
    existing = (
        await db.execute(
            sa_text(
                "SELECT file_hash FROM vault_chunks "
                "WHERE brain_label = :b AND path = :p LIMIT 1"
            ),
            {"b": brain_label, "p": rel_path},
        )
    ).first()
    if existing and existing[0] == fhash:
        # File unchanged; but back-fill embeddings that were skipped when the
        # embedding service was unavailable during a prior index run.
        has_null = (
            await db.execute(
                sa_text(
                    "SELECT 1 FROM vault_chunks "
                    "WHERE brain_label = :b AND path = :p AND embedding IS NULL LIMIT 1"
                ),
                {"b": brain_label, "p": rel_path},
            )
        ).first()
        if not has_null:
            return 0  # unchanged and fully embedded
        # Unverändert, nur Embeddings fehlen: NUR diese nachfüllen. Vorher wurde die
        # Datei neu zerlegt, gelöscht und neu geschrieben — ohne Embedding-Dienst
        # (oder mit falschem Schlüssel) alle 5 Minuten jede Datei aller Vaults.
        if nachfuellen is not None and nachfuellen.get("aus"):
            return 0
        return await _embeddings_nachfuellen(db, brain_label, rel_path, content, nachfuellen)

    chunks = chunk_markdown(content)
    # Replace all chunks of this file atomically.
    await db.execute(
        sa_text("DELETE FROM vault_chunks WHERE brain_label = :b AND path = :p"),
        {"b": brain_label, "p": rel_path},
    )
    if not chunks:
        await db.commit()
        return 0

    vectors = await _embed_chunks([c.embed_text for c in chunks])
    for c, vec in zip(chunks, vectors):
        params = {
            "b": brain_label,
            "p": rel_path,
            "i": c.index,
            "h": c.heading or None,
            "c": c.content,
            "fh": fhash,
        }
        if vec is not None:
            params["emb"] = str(vec)
            await db.execute(
                sa_text(
                    "INSERT INTO vault_chunks "
                    "(brain_label, path, chunk_idx, heading, content, file_hash, embedding) "
                    "VALUES (:b, :p, :i, :h, :c, :fh, CAST(:emb AS vector))"
                ),
                params,
            )
        else:
            await db.execute(
                sa_text(
                    "INSERT INTO vault_chunks "
                    "(brain_label, path, chunk_idx, heading, content, file_hash) "
                    "VALUES (:b, :p, :i, :h, :c, :fh)"
                ),
                params,
            )
    await db.commit()
    return len(chunks)


async def _embeddings_nachfuellen(
    db: AsyncSession, brain_label: str, rel_path: str, content: str, zustand: dict | None
) -> int:
    """Fehlende Embeddings einer unveränderten Datei setzen, ohne sie neu zu schreiben.

    Liefert der Dienst nichts, merkt sich ``zustand["aus"]`` das für den Rest des
    Laufs — sonst fragte jede weitere Datei einen Dienst an, der gerade fehlt.
    """
    leer = set(
        (
            await db.execute(
                sa_text(
                    "SELECT chunk_idx FROM vault_chunks "
                    "WHERE brain_label = :b AND path = :p AND embedding IS NULL"
                ),
                {"b": brain_label, "p": rel_path},
            )
        ).scalars().all()
    )
    teile = [c for c in chunk_markdown(content) if c.index in leer]
    if not teile:
        return 0
    vectors = await _embed_chunks([c.embed_text for c in teile])
    gesetzt = 0
    for c, vec in zip(teile, vectors):
        if vec is None:
            continue
        await db.execute(
            sa_text(
                "UPDATE vault_chunks SET embedding = CAST(:emb AS vector) "
                "WHERE brain_label = :b AND path = :p AND chunk_idx = :i"
            ),
            {"emb": str(vec), "b": brain_label, "p": rel_path, "i": c.index},
        )
        gesetzt += 1
    if gesetzt:
        await db.commit()
    elif zustand is not None:
        zustand["aus"] = True
    return 0


async def remove_file(db: AsyncSession, brain_label: str, rel_path: str) -> None:
    """Drop all chunks for a deleted vault file."""
    await db.execute(
        sa_text("DELETE FROM vault_chunks WHERE brain_label = :b AND path = :p"),
        {"b": brain_label, "p": rel_path},
    )
    await db.commit()


async def reindex_vault(
    db: AsyncSession, brain_label: str, host_path: str
) -> dict[str, int]:
    """Full (incremental) re-index of a vault. Returns simple stats.

    Prunes chunks whose source file no longer exists, then indexes every file
    (unchanged files are skipped via the file-hash check).
    """
    base = os.path.realpath(host_path)
    if not os.path.isdir(base):
        return {"files": 0, "chunks": 0, "pruned": 0}

    present = {rel for _, rel in _iter_files(base)}

    known = (
        await db.execute(
            sa_text("SELECT DISTINCT path FROM vault_chunks WHERE brain_label = :b"),
            {"b": brain_label},
        )
    ).scalars().all()
    pruned = 0
    for stale in set(known) - present:
        await remove_file(db, brain_label, stale)
        pruned += 1

    files = chunks = errors = 0
    nachfuellen: dict = {}
    for rel in present:
        try:
            n = await index_file(db, brain_label, host_path, rel, nachfuellen)
            if n:
                files += 1
                chunks += n
        except Exception:
            logger.warning("[VaultIndexer] skipping %s due to error", rel, exc_info=True)
            errors += 1
            # Ohne Rollback bliebe die Sitzung abgebrochen, und jede weitere Datei
            # (auch die anderer Vaults im selben Lauf) scheiterte gleich mit.
            await db.rollback()
    logger.info(
        "[VaultIndexer] reindexed brain=%s files=%s chunks=%s pruned=%s errors=%s",
        brain_label, files, chunks, pruned, errors,
    )
    return {"files": files, "chunks": chunks, "pruned": pruned, "errors": errors}
