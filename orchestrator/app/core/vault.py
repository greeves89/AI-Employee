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
import re

# Files larger than this are skipped (search) / refused (read) — vaults are
# Markdown knowledge bases, not binary stores.
MAX_FILE_BYTES = 2_000_000

_SEARCHABLE_SUFFIXES = (".md", ".markdown", ".txt")

# Link/tag extraction for the Obsidian-style graph view.
# Wikilink: [[Note]], [[Note|Alias]], [[Note#Heading]] — capture the target part.
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
# Relative markdown link to another note: [text](some/Note.md)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+\.(?:md|markdown))\)")
# Inline #tag (not a markdown heading: requires a word char right after #)
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9_][A-Za-z0-9_/-]{1,39})")
# First level-1 heading → human title for a note
_HEADING_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)

# Soft cap so a pathologically large vault can't stall the canvas force sim.
MAX_GRAPH_NODES = 2000


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


# --- Write side (used by the MCP endpoint when the brain is rw) ---------------

_WRITABLE_SUFFIXES = (".md", ".markdown", ".txt")


def write_file(host_path: str, rel_path: str, content: str, *, overwrite: bool = True) -> dict:
    """Create or overwrite a Markdown note in the vault (jailed).

    Only text/markdown files may be written — the vault is a knowledge base, not
    a binary store. Parent folders are created. Write is atomic (tmp + rename).
    Raises ``ValueError`` (bad path/suffix/size) or ``FileExistsError`` (overwrite=False).
    """
    target = resolve_path(host_path, rel_path)
    if not target.lower().endswith(_WRITABLE_SUFFIXES):
        raise ValueError("only .md/.markdown/.txt files can be written")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("content too large")
    existed = os.path.isfile(target)
    if existed and not overwrite:
        raise FileExistsError(rel_path)
    os.makedirs(os.path.dirname(target) or os.path.realpath(host_path), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, target)
    return {"path": rel_path, "created": not existed, "bytes": len(content.encode("utf-8"))}


def delete_file(host_path: str, rel_path: str) -> None:
    """Delete a vault file (jailed). Raises ``FileNotFoundError`` / ``ValueError``."""
    target = resolve_path(host_path, rel_path)
    if not os.path.isfile(target):
        raise FileNotFoundError(rel_path)
    os.remove(target)


def tree_text(host_path: str) -> str:
    """Render the vault folder/file structure as an indented tree (from list_entries)."""
    entries = list_entries(host_path)
    if not entries:
        return "(empty)"
    lines = []
    for e in entries:
        depth = e["path"].count(os.sep)
        indent = "  " * depth
        marker = "/" if e["type"] == "dir" else ""
        lines.append(f"{indent}{e['name']}{marker}")
    return "\n".join(lines)


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


def _title_of(rel: str, content: str) -> str:
    """Human label for a note: first ``# Heading`` if present, else filename stem."""
    m = _HEADING_RE.search(content)
    if m:
        return m.group(1).strip()
    return os.path.splitext(os.path.basename(rel))[0]


def build_graph(host_path: str) -> dict:
    """Obsidian-style link graph for the vault.

    Nodes = Markdown notes; edges = ``[[wikilinks]]`` and relative ``.md`` links
    between them. Targets resolve by title, full relative path (without ext), or
    bare filename stem (case-insensitive) — mirroring how Obsidian resolves
    ``[[Note Name]]``. Pure filesystem + regex, no DB/embedding dependency, so it
    works wherever the vault is mounted. Returns ``{"nodes": [...], "edges": [...]}``.
    """
    base = os.path.realpath(host_path)
    if not os.path.isdir(base):
        return {"nodes": [], "edges": []}

    files: list[dict] = []           # {rel, name, folder, content, tags}
    by_stem: dict[str, str] = {}     # filename stem (lower) -> rel
    by_title: dict[str, str] = {}    # title (lower) -> rel
    by_relstem: dict[str, str] = {}  # rel without ext (lower, /) -> rel
    truncated = False

    for root, dirs, fnames in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for f in sorted(fnames):
            if not f.lower().endswith((".md", ".markdown")):
                continue
            if len(files) >= MAX_GRAPH_NODES:
                truncated = True
                break
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            stem = os.path.splitext(os.path.basename(rel))[0]
            title = _title_of(rel, content)
            parts = rel.split(os.sep)
            folder = parts[0] if len(parts) > 1 else ""
            tags = sorted(set(_TAG_RE.findall(content)))[:12]
            files.append({
                "rel": rel, "name": title or stem, "folder": folder,
                "content": content, "tags": tags,
            })
            by_stem.setdefault(stem.lower(), rel)
            if title:
                by_title.setdefault(title.lower(), rel)
            by_relstem.setdefault(os.path.splitext(rel)[0].lower().replace(os.sep, "/"), rel)
        if truncated:
            break

    def _resolve(target: str) -> str | None:
        t = (target or "").strip().lower().replace("\\", "/")
        if not t:
            return None
        if t in by_title:
            return by_title[t]
        if t in by_relstem:
            return by_relstem[t]
        if t in by_stem:
            return by_stem[t]
        # path-style target: fall back to its bare filename stem
        bn = os.path.splitext(os.path.basename(t))[0]
        return by_stem.get(bn)

    indeg: dict[str, int] = {fr["rel"]: 0 for fr in files}
    outdeg: dict[str, int] = {fr["rel"]: 0 for fr in files}
    edges: list[dict] = []
    seen: set[tuple] = set()

    for fr in files:
        src = fr["rel"]
        targets = set(_WIKILINK_RE.findall(fr["content"]))
        targets.update(_MD_LINK_RE.findall(fr["content"]))
        for tgt in targets:
            dst = _resolve(tgt)
            if not dst or dst == src or (src, dst) in seen:
                continue
            seen.add((src, dst))
            edges.append({"source": src, "target": dst})
            outdeg[src] += 1
            indeg[dst] += 1

    nodes = [{
        "id": fr["rel"],
        "name": fr["name"],
        "path": fr["rel"],
        "folder": fr["folder"],
        "tags": fr["tags"],
        "in": indeg.get(fr["rel"], 0),
        "out": outdeg.get(fr["rel"], 0),
        "degree": indeg.get(fr["rel"], 0) + outdeg.get(fr["rel"], 0),
    } for fr in files]

    return {"nodes": nodes, "edges": edges, "truncated": truncated}
