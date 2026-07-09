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

# ── Rolling-summary constants ─────────────────────────────────────────────────
# Absolute per-call token budget that triggers compaction, regardless of how
# large the model's context window is. gpt-5.x has a 1,000,000-token window, so
# the old "75% of window" gate (750k) was effectively never reached — every turn
# re-sent the full, growing history and cumulative input cost exploded. Capping
# at an absolute budget keeps each call cheap and makes the rolling summary
# kick in regularly on long agentic tasks.
ABSOLUTE_COMPACTION_BUDGET = 150_000

# Sliding window: how many of the most recent messages are kept VERBATIM.
# Tool-using agents need the exact recent tool I/O (file paths, IDs, values) to
# act on it — these must never be summarized. ~24 msgs ≈ the last dozen tool
# rounds. Everything older is folded into the rolling summary.
RECENT_WINDOW_MESSAGES = 24

# Marker that identifies the single rolling-summary message in the history, so
# the next compaction EXTENDS it incrementally instead of re-summarizing from
# scratch.
ROLLING_SUMMARY_PREFIX = "[Conversation summary — rolling context]"


def effective_threshold_tokens(context_window: int) -> int:
    """Token count at which compaction should fire.

    The smaller of 75% of the model window or the absolute budget. On huge
    windows the absolute budget wins (keeps calls cheap); on small windows the
    75% mark wins (leaves headroom before a hard overflow).
    """
    return min(int(context_window * 0.75), ABSOLUTE_COMPACTION_BUDGET)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(messages: list["ChatMessage"]) -> int:
    """Rough token estimate: total chars / 4 (image blocks fixed at ~1.5k)."""
    total = 0
    image_tokens = 0
    for m in messages:
        if isinstance(m.content, str) and m.content:
            total += len(m.content)
        elif isinstance(m.content, list):
            for c in m.content:
                if isinstance(c, dict) and c.get("type") == "image":
                    image_tokens += 1500
                else:
                    total += len(str(c))
        if getattr(m, "tool_calls", None):
            total += len(json.dumps(m.tool_calls))
    return total // 4 + image_tokens


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

INCREMENTAL_COMPACTION_PROMPT = (
    "You are maintaining a ROLLING summary of a long agent work session.\n\n"
    "Below you get (a) the summary so far — may be empty — and (b) a transcript "
    "of older messages that are about to scroll out of the live context window. "
    "The most recent messages are NOT shown here; they stay verbatim in context.\n\n"
    "Produce an UPDATED summary that MERGES both: keep everything from the prior "
    "summary that is still relevant and fold in the older messages. The result "
    "must let the assistant continue seamlessly. Be specific and preserve every "
    "actionable detail:\n"
    "- The user's original goal(s) and any changed requirements\n"
    "- Concrete artifacts: file paths, function names, commands run, IDs, URLs, exact values\n"
    "- Decisions made and the reasoning behind them\n"
    "- Errors encountered and how they were resolved\n"
    "- What is still pending or in progress\n\n"
    "Write prose (not JSON). Do NOT lose any actionable information. "
    "Do NOT invent facts that are not in the prior summary or transcript."
)


def _serialize_for_summary(messages: list["ChatMessage"]) -> str:
    """Flatten a message list into a plain-text transcript for summarization.

    Sending the block as ONE user message (instead of replaying real tool/
    assistant turns) keeps the summarize call provider-agnostic and avoids any
    tool-call protocol pitfalls. Images are reduced to a placeholder.
    """
    lines: list[str] = []
    for m in messages:
        if isinstance(m.content, str):
            text = m.content
        elif isinstance(m.content, list):
            parts = []
            for c in m.content:
                if isinstance(c, dict) and c.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(str(c))
            text = " ".join(parts)
        else:
            text = ""
        if getattr(m, "tool_calls", None):
            tc = json.dumps(m.tool_calls)
            text += f" [tool_calls: {tc[:2000]}]"
        lines.append(f"{m.role.upper()}: {text}")
    return "\n\n".join(lines)


async def summarize_messages(
    messages: list["ChatMessage"],
    provider: "BaseLLMProvider",
    keep_recent: int = RECENT_WINDOW_MESSAGES,
    rescue_key: str | None = None,
) -> list["ChatMessage"] | None:
    """Layer 4 — Sliding-window + incremental rolling summary.

    Instead of discarding the whole conversation, this:
      1. keeps the system message,
      2. keeps the last ``keep_recent`` messages VERBATIM (recent tool I/O the
         agent still needs to act on — exact paths, IDs, values),
      3. folds everything older into a single rolling-summary message, EXTENDING
         the previous rolling summary if one already exists (incremental — the
         older messages are only ever summarized once).

    Result layout: ``[system?] + [rolling-summary assistant] + [recent verbatim]``.

    Returns the new (shorter) message list, or ``None`` if there is nothing old
    enough to summarize / on failure (caller keeps the current history).
    """
    from app.providers.base import ChatMessage  # local import to avoid circularity

    # 1. Peel the system message.
    system_msg = messages[0] if messages and messages[0].role == "system" else None
    body = list(messages[1:]) if system_msg else list(messages)

    # 2. Peel an existing rolling summary so we EXTEND it instead of redoing it.
    prior_summary = ""
    if (
        body
        and body[0].role == "assistant"
        and isinstance(body[0].content, str)
        and body[0].content.startswith(ROLLING_SUMMARY_PREFIX)
    ):
        prior_summary = body[0].content[len(ROLLING_SUMMARY_PREFIX):].strip()
        body = body[1:]

    # 3. Nothing old enough? The recent window already covers everything.
    if len(body) <= keep_recent:
        return None

    to_summarize = body[:-keep_recent]
    recent = body[-keep_recent:]

    # 4. Never orphan a tool result: the recent window must not START with a
    #    tool message whose originating tool_call got summarized away. Push such
    #    leading tool results back into the summarized block.
    while recent and recent[0].role == "tool":
        to_summarize.append(recent.pop(0))
    if not recent or not to_summarize:
        return None  # degenerate (e.g. one giant tool-call run) — leave as-is

    # 5. Summarize the old block, merging with the prior rolling summary.
    transcript = _serialize_for_summary(to_summarize)
    user_block = (
        f"=== SUMMARY SO FAR ===\n{prior_summary or '(none yet)'}\n\n"
        f"=== OLDER MESSAGES TO FOLD IN ===\n{transcript}"
    )
    compact_input = [
        ChatMessage(role="system", content=INCREMENTAL_COMPACTION_PROMPT),
        ChatMessage(role="user", content=user_block),
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

    # 6. Rebuild: [system?] + [rolling summary] + [recent verbatim].
    new_msgs: list[ChatMessage] = []
    if system_msg:
        new_msgs.append(system_msg)
    new_msgs.append(ChatMessage(
        role="assistant",
        content=f"{ROLLING_SUMMARY_PREFIX}\n\n{summary.strip()}",
    ))
    new_msgs.extend(recent)

    logger.info(
        f"[Summarize] rolling: {len(messages)} msgs → {len(new_msgs)} msgs "
        f"(summarized {len(to_summarize)}, kept {len(recent)} verbatim, "
        f"{len(summary)} chars summary)"
    )

    # Compaction rescue: the folded-away context would otherwise be lost forever.
    # Persist the rolling summary to long-term memory (source=compaction) —
    # fire-and-forget, override=True so each extension supersedes the previous
    # snapshot in the same bucket (the supersede chain keeps the history).
    try:
        import asyncio
        from app.tools.api_client import OrchestratorAPIClient
        _client = OrchestratorAPIClient()
        _payload = {
            "category": "task_context",
            "key": f"compaction:{rescue_key or 'rolling'}",
            "content": f"[Kompaktierter Kontext] {summary.strip()[:3500]}",
            "importance": 2,
            "room": "compaction",
            "tag_type": "transient",
            "source": "compaction",
            "override": True,
        }
        asyncio.get_running_loop().create_task(_client.memory_save(_payload))
    except Exception as exc:  # noqa: BLE001 — rescue must never break compaction
        logger.debug(f"[Summarize] compaction rescue skipped: {exc}")

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
