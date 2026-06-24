"""Shared helpers for Second Brain Markdown vaults.

Used by BOTH the admin file-browser API (``app/api/brains.py``) and the
per-brain MCP server (``app/api/brain_mcp.py``) so the path-jailing and search
logic lives in exactly one place. Every path is resolved against the vault root
and can never escape it or touch the ``.git`` directory.

The orchestrator reaches the vault files directly because ``/srv/secondbrain``
is bind-mounted into the orchestrator (same mount used for provisioning).
"""
from __future__ import annotations

import os

# Files larger than this are skipped (search) / refused (read) — vaults are
# Markdown knowledge bases, not binary stores.
MAX_FILE_BYTES = 2_000_000

_SEARCHABLE_SUFFIXES = (".md", ".markdown", ".txt")


def resolve_path(host_path: str, rel_path: str) -> str:
    """Resolve a vault-relative path to an absolute host path, jailed to the
    vault root and never touching ``.git``. Raises ``ValueError`` on escape."""
    base = os.path.realpath(host_path)
    target = os.path.realpath(os.path.join(base, (rel_path or "").lstrip("/")))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("path escapes the vault")
    if ".git" in os.path.relpath(target, base).split(os.sep):
        raise ValueError(".git is not accessible")
    return target


def list_entries(host_path: str) -> list[dict]:
    """Flat, sorted list of folders + files in the vault (excluding ``.git``)."""
    base = os.path.realpath(host_path)
    entries: list[dict] = []
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            for d in dirs:
                rel = os.path.relpath(os.path.join(root, d), base)
                entries.append({"path": rel, "name": d, "type": "dir"})
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), base)
                entries.append({"path": rel, "name": f, "type": "file"})
    entries.sort(key=lambda e: e["path"].lower())
    return entries


def read_file(host_path: str, rel_path: str) -> str:
    """Read a vault file (jailed). Raises ``FileNotFoundError`` / ``ValueError``."""
    target = resolve_path(host_path, rel_path)
    if not os.path.isfile(target):
        raise FileNotFoundError(rel_path)
    if os.path.getsize(target) > MAX_FILE_BYTES:
        raise ValueError("file too large to read")
    with open(target, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def search(host_path: str, query: str, limit: int = 10) -> list[dict]:
    """Case-insensitive grep over the vault's Markdown/text files.

    Matches against both file path and content; filename hits weigh more.
    Returns ``[{path, score, snippets}]`` sorted by score (desc). This is the
    deliberate "boardmittel" approach — plain grep over the .md collection, no
    embedding/DB dependency — so the MCP endpoint works even if pgvector is down.
    """
    base = os.path.realpath(host_path)
    terms = [t for t in (query or "").strip().lower().split() if t]
    if not terms or not os.path.isdir(base):
        return []

    results: list[dict] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if not f.lower().endswith(_SEARCHABLE_SUFFIXES):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue

            name_hay = rel.lower()
            hay = content.lower()
            score = 0
            for term in terms:
                score += name_hay.count(term) * 5  # filename hits weigh more
                score += hay.count(term)
            if score <= 0:
                continue

            snippets: list[str] = []
            for line in content.splitlines():
                low = line.lower()
                if any(t in low for t in terms):
                    s = line.strip()
                    if s:
                        snippets.append(s[:240])
                    if len(snippets) >= 5:
                        break
            results.append({"path": rel, "score": score, "snippets": snippets})

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
