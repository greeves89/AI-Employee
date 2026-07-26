"""Markdown-aware chunking for Second Brain vault indexing.

Pure, dependency-free, unit-testable. Splits a Markdown document into
passage-sized chunks (1-3 paragraphs) so retrieval can return the relevant
*passage* instead of the whole file — the missing "chunking" layer from the
RAG maturity model (Naive -> Advanced -> Agentic).

Design goals:
  * Never split in the middle of a fenced code block (``` ... ```).
  * Prefer splitting on Markdown headings, then on blank-line paragraph breaks.
  * Keep the nearest preceding heading as lightweight context on every chunk so
    a passage stays interpretable on its own.
  * Deterministic: same input -> same chunks (important for incremental
    re-indexing keyed on a content hash).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^(```|~~~)")

# Target sizes in characters. A "chunk" is 1-3 paragraphs; we merge small
# paragraphs up to TARGET and hard-split anything above MAX.
TARGET_CHARS = 900
MAX_CHARS = 1400


@dataclass
class Chunk:
    """One retrievable passage of a vault file."""

    index: int
    heading: str  # nearest preceding heading path, e.g. "Drucker > Fehlercodes"
    content: str  # the passage text (without the injected heading)

    @property
    def embed_text(self) -> str:
        """Text handed to the embedding model — heading gives context."""
        if self.heading:
            return f"{self.heading}\n\n{self.content}"
        return self.content


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """Split raw markdown into (kind, text) blocks.

    kind is one of: 'heading', 'code', 'para'. Fenced code blocks are kept
    intact as a single block so they never get cut mid-fence.
    """
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    buf: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush_para() -> None:
        if buf:
            chunk = "\n".join(buf).strip()
            if chunk:
                blocks.append(("para", chunk))
            buf.clear()

    for line in lines:
        fence = _FENCE_RE.match(line.strip())
        if in_fence:
            buf.append(line)
            if line.strip().startswith(fence_marker):
                blocks.append(("code", "\n".join(buf)))
                buf.clear()
                in_fence = False
            continue
        if fence:
            flush_para()
            fence_marker = fence.group(1)
            buf.append(line)
            in_fence = True
            continue
        if _HEADING_RE.match(line):
            flush_para()
            blocks.append(("heading", line))
            continue
        if line.strip() == "":
            flush_para()
            continue
        buf.append(line)

    if in_fence:  # unterminated fence — keep what we have
        blocks.append(("code", "\n".join(buf)))
    else:
        flush_para()
    return blocks


def _heading_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(title for _, title in stack)


def _hard_split(content: str, max_chars: int) -> list[str]:
    """Split an oversized block on paragraph/sentence/line boundaries."""
    if len(content) <= max_chars:
        return [content]
    parts: list[str] = []
    remaining = content
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
        if cut < max_chars // 2:
            cut = max_chars  # no good boundary — hard cut
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return [p for p in parts if p]


def chunk_markdown(text: str) -> list[Chunk]:
    """Chunk a Markdown document into passage-sized :class:`Chunk` objects."""
    if not text or not text.strip():
        return []

    blocks = _split_blocks(text)
    heading_stack: list[tuple[int, str]] = []
    chunks: list[Chunk] = []
    cur_parts: list[str] = []
    cur_len = 0
    cur_heading = ""

    def emit() -> None:
        nonlocal cur_parts, cur_len
        if not cur_parts:
            return
        body = "\n\n".join(cur_parts).strip()
        cur_parts = []
        cur_len = 0
        # Drop pure noise (horizontal rules, stray punctuation) but keep short
        # real passages — a two-line note is still worth retrieving.
        if not any(ch.isalnum() for ch in body):
            return
        for piece in _hard_split(body, MAX_CHARS):
            if any(ch.isalnum() for ch in piece):
                chunks.append(Chunk(index=len(chunks), heading=cur_heading, content=piece))

    for kind, btext in blocks:
        if kind == "heading":
            emit()
            m = _HEADING_RE.match(btext)
            level = len(m.group(1))
            title = m.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            cur_heading = _heading_path(heading_stack)
            continue

        blen = len(btext)
        # Oversized code blocks must never be hard-split (would produce unbalanced fences).
        if kind == "code" and blen > MAX_CHARS:
            emit()
            if any(ch.isalnum() for ch in btext):
                chunks.append(Chunk(index=len(chunks), heading=cur_heading, content=btext))
            continue

        # code (small) or para
        if cur_len and cur_len + blen > TARGET_CHARS:
            emit()
        cur_parts.append(btext)
        cur_len += blen
        if cur_len >= TARGET_CHARS:
            emit()

    emit()
    # renumber to be safe (hard_split may have added extras)
    for i, c in enumerate(chunks):
        c.index = i
    return chunks
