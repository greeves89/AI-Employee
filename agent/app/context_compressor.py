"""4-Layer Context Compression Pipeline for custom LLM providers.

Layers run in order, each only if the previous did not bring usage below
the target threshold.  Claude Code CLI agents handle their own compaction
natively — this module is for the custom LLM (LLMRunner / LLMChatHandler)
code path only.

Pipeline:
  Layer 1 — Snip        : truncate oversized tool outputs in-place (lossless)
  Layer 2 — Microcompact: strip assistant verbosity (near-lossless)
  Layer 3 — Collapse    : merge consecutive identical tool calls (near-lossless)
  Layer 4 — Summarize   : LLM-based abstractive summary (lossy, last resort)

Each layer returns the modified message list and a flag indicating whether it
made any changes. The caller decides when to stop based on token estimates.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.base import BaseLLMProvider, ChatMessage

logger = logging.getLogger(__name__)

# ── Snip constants ────────────────────────────────────────────────────────────
# Tool outputs longer than this are snipped: keep head + tail + summary line.
SNIP_THRESHOLD_CHARS = 1_200
SNIP_HEAD_CHARS = 400
SNIP_TAIL_CHARS = 200

# ── Microcompact patterns ─────────────────────────────────────────────────────
# Verbose assistant preambles that carry zero information.
_PREAMBLE_RE = re.compile(
    r"^(I('ll| will| am going to)|Let me|Sure[,!]?\s|Of course[,!]?\s|"
    r"Certainly[,!]?\s|Great[,!]?\s|Absolutely[,!]?\s)[^\n]*\n?",
    re.IGNORECASE | re.MULTILINE,
)
# "I have successfully …" — typically just padding at the end of a response.
_SUCCESS_RE = re.compile(
    r"\n?(I have (successfully |now )?(completed|finished|done|implemented|"
    r"created|updated|fixed|added|removed|written)[^\n]*\.?\s*)+$",
    re.IGNORECASE,
)

# ── Collapse constants ────────────────────────────────────────────────────────
# If the same tool is called with the same arguments N times in a row,
# collapse them into one entry with a "(×N)" annotation.
COLLAPSE_MIN_REPEATS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(messages: list["ChatMessage"]) -> int:
    """Rough token estimate: total chars / 4."""
    total = 0
    for m in messages:
        if isinstance(m.content, str) and m.content:
            total += len(m.content)
        elif isinstance(m.content, list):
            total += sum(len(str(c)) for c in m.content)
        if getattr(m, "tool_calls", None):
            total += len(json.dumps(m.tool_calls))
    return total // 4


def compress_messages(
    messages: list["ChatMessage"],
    context_window: int,
    target_pct: float = 0.55,
) -> tuple[list["ChatMessage"], list[str]]:
    """Run the 3 deterministic layers (Snip, Microcompact, Collapse).

    Returns (compressed_messages, list_of_layers_applied).
    The Summarize layer is async and handled separately by the caller.

    target_pct: stop compressing once we're below this fraction of the window.
    """
    target_tokens = int(context_window * target_pct)
    applied: list[str] = []

    # Layer 1 — Snip
    msgs, changed = _snip(messages)
    if changed:
        applied.append("snip")
        messages = msgs
        if estimate_tokens(messages) <= target_tokens:
            return messages, applied

    # Layer 2 — Microcompact
    msgs, changed = _microcompact(messages)
    if changed:
        applied.append("microcompact")
        messages = msgs
        if estimate_tokens(messages) <= target_tokens:
            return messages, applied

    # Layer 3 — Collapse
    msgs, changed = _collapse(messages)
    if changed:
        applied.append("collapse")
        messages = msgs

    return messages, applied


# ── COMPACTION PROMPT (used by the async Summarize layer in callers) ──────────

COMPACTION_PROMPT = (
    "You are a conversation compactor. Summarize the ENTIRE conversation so far "
    "into a concise but complete status report. Include:\n"
    "1. What the user originally asked for\n"
    "2. What has been accomplished so far (files changed, commands run, decisions made)\n"
    "3. What is still pending or in progress\n"
    "4. Any errors encountered and how they were resolved\n"
    "5. Key context the assistant needs to continue seamlessly\n\n"
    "Write the summary as a briefing for the assistant to continue the work. "
    "Be specific — include file paths, function names, and concrete details. "
    "Do NOT lose any actionable information."
)


async def summarize_messages(
    messages: list["ChatMessage"],
    provider: "BaseLLMProvider",
) -> list["ChatMessage"] | None:
    """Layer 4 — Summarize: ask the LLM to produce a compact summary.

    Returns the new (shorter) message list, or None on failure.
    Preserves the system message and the last user message.
    """
    from app.providers.base import ChatMessage  # local import to avoid circularity

    compact_input = list(messages) + [
        ChatMessage(role="user", content=COMPACTION_PROMPT)
    ]

    summary = ""
    try:
        async for event in provider.stream_completion(compact_input, tools=None):
            if event.type == "text_delta":
                summary += event.text
            elif event.type == "error":
                logger.error(f"[Summarize] LLM error: {event.text}")
                return None
    except Exception as exc:
        logger.error(f"[Summarize] Exception: {exc}")
        return None

    if not summary.strip():
        logger.warning("[Summarize] Empty summary returned")
        return None

    # Rebuild: [system?] + [summary-as-assistant] + [last user msg]
    system_msg = messages[0] if messages and messages[0].role == "system" else None
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)

    new_msgs: list[ChatMessage] = []
    if system_msg:
        new_msgs.append(system_msg)
    new_msgs.append(ChatMessage(
        role="assistant",
        content=f"[Conversation summary — context compacted]\n\n{summary}",
    ))
    if last_user:
        new_msgs.append(last_user)

    logger.info(
        f"[Summarize] {len(messages)} msgs → {len(new_msgs)} msgs "
        f"({len(summary)} chars summary)"
    )
    return new_msgs


# ─────────────────────────────────────────────────────────────────────────────
# Layer implementations
# ─────────────────────────────────────────────────────────────────────────────

def _snip(messages: list["ChatMessage"]) -> tuple[list["ChatMessage"], bool]:
    """Layer 1 — Snip oversized tool results.

    Tool results are often huge (test output, directory listings, API dumps).
    We keep the first SNIP_HEAD_CHARS and last SNIP_TAIL_CHARS and insert a
    summary line in the middle. The LLM still sees the important parts.
    """
    changed = False
    result = []
    for msg in messages:
        if msg.role == "tool" and isinstance(msg.content, str):
            content = msg.content
            if len(content) > SNIP_THRESHOLD_CHARS:
                head = content[:SNIP_HEAD_CHARS]
                tail = content[-SNIP_TAIL_CHARS:] if SNIP_TAIL_CHARS else ""
                skipped = len(content) - SNIP_HEAD_CHARS - SNIP_TAIL_CHARS
                snipped = (
                    f"{head}\n"
                    f"[... {skipped:,} chars snipped by context compressor ...]\n"
                    f"{tail}"
                )
                from copy import copy
                new_msg = copy(msg)
                new_msg.content = snipped
                result.append(new_msg)
                changed = True
                continue
        result.append(msg)
    return result, changed


def _microcompact(messages: list["ChatMessage"]) -> tuple[list["ChatMessage"], bool]:
    """Layer 2 — Strip verbose assistant preambles and success footers.

    Targets text like:
      "Let me help you with that. I'll start by..."
      "I have successfully completed all the requested changes."

    These carry no information the LLM needs for future reasoning.
    """
    changed = False
    result = []
    for msg in messages:
        if msg.role == "assistant" and isinstance(msg.content, str) and msg.content:
            text = msg.content
            # Remove verbose preamble lines at the start
            new_text = _PREAMBLE_RE.sub("", text)
            # Remove "I have successfully …" closing lines
            new_text = _SUCCESS_RE.sub("", new_text)
            # Collapse multiple blank lines into one
            new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip()
            if new_text != text:
                from copy import copy
                new_msg = copy(msg)
                new_msg.content = new_text or text  # never produce empty
                result.append(new_msg)
                changed = True
                continue
        result.append(msg)
    return result, changed


def _collapse(messages: list["ChatMessage"]) -> tuple[list["ChatMessage"], bool]:
    """Layer 3 — Collapse repeated consecutive tool calls.

    If the same tool is invoked with the same arguments N≥3 times in a row
    (e.g. a stuck read_file loop), replace them with a single entry annotated
    "(×N duplicate calls collapsed)".

    Also collapses consecutive identical error messages from tool results.
    """
    from copy import copy

    changed = False
    result: list["ChatMessage"] = []

    i = 0
    while i < len(messages):
        msg = messages[i]

        # Check for consecutive identical tool calls
        if msg.role == "assistant" and getattr(msg, "tool_calls", None):
            sig = json.dumps(msg.tool_calls, sort_keys=True)
            j = i + 1
            while j < len(messages):
                cand = messages[j]
                if cand.role == "assistant" and json.dumps(
                    getattr(cand, "tool_calls", None), sort_keys=True
                ) == sig:
                    j += 1
                else:
                    break
            repeat_count = j - i
            if repeat_count >= COLLAPSE_MIN_REPEATS:
                new_msg = copy(msg)
                # Annotate the tool call input with the repeat count
                if new_msg.tool_calls:
                    annotated = list(new_msg.tool_calls)
                    note = f" [×{repeat_count} identical calls — collapsed by context compressor]"
                    for tc in annotated:
                        if isinstance(tc, dict) and "function" in tc:
                            tc["function"]["_note"] = note
                result.append(new_msg)
                # Skip the corresponding tool result messages too
                # (they follow: tool_result for each call)
                k = j
                tool_result_count = 0
                while k < len(messages) and tool_result_count < repeat_count - 1:
                    if messages[k].role == "tool":
                        tool_result_count += 1
                        k += 1
                    else:
                        break
                i = k
                changed = True
                continue

        # Check for consecutive identical tool results (same error repeated)
        if msg.role == "tool" and isinstance(msg.content, str):
            j = i + 1
            while (
                j < len(messages)
                and messages[j].role == "tool"
                and messages[j].content == msg.content
            ):
                j += 1
            repeat_count = j - i
            if repeat_count >= COLLAPSE_MIN_REPEATS:
                new_msg = copy(msg)
                new_msg.content = (
                    f"{msg.content}\n"
                    f"[×{repeat_count} identical results — collapsed by context compressor]"
                )
                result.append(new_msg)
                i = j
                changed = True
                continue

        result.append(msg)
        i += 1

    return result, changed
