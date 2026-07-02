"""LLM Chat Handler - interactive chat sessions using custom LLM providers."""

import asyncio
import json
import logging
import time

from app import context_compressor, model_registry, multimodal
from app.loop_detector import LoopDetector
from app.config import settings
from app.log_publisher import LogPublisher
from app.providers import create_provider
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent
from app.tools.definitions import TOOL_DEFINITIONS
from app.tools.executor import ToolExecutor
from app.tools.mcp_client import MCPHTTPClient

logger = logging.getLogger(__name__)

# Tool-loop cap per chat message. Honors the admin-configured
# "Max Turns per Task" setting (settings.max_turns); the constant is
# only a fallback if that is somehow unset. A real agent stops on its
# own long before the cap, and tight repeat-loops are caught separately
# by LoopDetector — so the cap is just a runaway backstop.
DEFAULT_MAX_TURNS = 100


def _max_turns() -> int:
    return settings.max_turns if settings.max_turns and settings.max_turns > 0 else DEFAULT_MAX_TURNS


# --- Lazy tool loading -------------------------------------------------------
# OpenAI/Azure cap function tools at 128 PER REQUEST. Instead of sending the whole
# catalog (18 built-in + 41 orchestrator API + every MCP tool), we send only a small
# CORE set + a `search_tools` meta-tool, and ACTIVATE specific tools on demand when
# the model searches for them. So the catalog can grow without limit.
CORE_TOOL_NAMES = {
    "bash", "read_file", "write_file", "edit_file", "multi_edit",
    "list_files", "grep", "glob", "git_status", "git_diff",
    "web_search", "web_fetch",
    "request_approval", "notify_user", "send_message_and_wait",
    "memory_save", "memory_search", "brain_search", "secondbrain_search",
    "list_todos", "complete_todo", "update_todos",
    # The standard task workflow MANDATES a skill check + rating on every task, so these
    # must always be loaded — otherwise the agent hits "tool not available" mid-workflow
    # (it cannot search_tools for a capability the workflow already required).
    "skill_search", "skill_install", "skill_rate", "skill_propose", "rate_task",
}
MAX_ACTIVATED_TOOLS = 60  # core (~27) + search_tools + activated stays well under 128

SEARCH_TOOLS_DEF = {
    "type": "function",
    "function": {
        "name": "search_tools",
        "description": (
            "Find and load ADDITIONAL tools by capability when none of your currently "
            "available tools fit the task. Searches the full catalog (Microsoft 365 — "
            "mail, calendar, Teams, OneDrive, Planner; the knowledge base; skills; other "
            "integrations) and makes the best matches callable on your NEXT step. Describe "
            "what you want to do, e.g. 'create a folder in OneDrive' or 'send a Teams message'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you want to do (capability or keywords)."},
            },
            "required": ["query"],
        },
    },
}


def _tokenize(text: str) -> list[str]:
    cleaned = "".join(c if c.isalnum() else " " for c in str(text).lower())
    return cleaned.split()


def _search_catalog(catalog: list[dict], query: str, exclude: set[str], limit: int = 8) -> list[dict]:
    """Keyword-rank tools by query terms over name + description (name hits weigh more)."""
    terms = [t for t in _tokenize(query) if len(t) >= 2]
    if not terms:
        return []
    scored: list[tuple[int, dict]] = []
    for tool in catalog:
        fn = tool.get("function", {})
        name = (fn.get("name") or "")
        if name in exclude:
            continue
        name_l = name.lower()
        hay = name_l + " " + (fn.get("description") or "").lower()
        score = sum(hay.count(term) for term in terms) + sum(2 for term in terms if term in name_l)
        if score > 0:
            scored.append((score, tool))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:limit]]


class LLMChatHandler:
    """Handles interactive chat sessions using custom LLM providers.

    Same interface as ChatHandler — publishes identical events via
    LogPublisher.publish_chat() so the frontend sees no difference.

    Context management: tracks token usage vs an absolute compaction budget
    (context_compressor.effective_threshold_tokens). When exceeded, it runs the
    deterministic compression layers and then a sliding-window rolling summary
    (recent messages kept verbatim, older ones folded into an extending
    summary), then continues seamlessly.
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.is_running = False
        self._provider: BaseLLMProvider | None = None
        self._tool_executor = ToolExecutor()
        self._mcp_client = MCPHTTPClient()
        # Execute MCP tool calls on the same client that ran discovery (shared
        # registry) — otherwise every MCP call fails with "Unknown MCP tool".
        self._tool_executor._mcp_client = self._mcp_client
        self._all_tools: list[dict] | None = None   # full catalog (cached), searchable
        # Tools loaded on demand via search_tools (recency order; capped). Only these
        # plus the CORE set are actually sent to the LLM — keeps us under the 128 cap.
        self._activated: list[str] = []
        # Conversation history (in-memory, replaces --resume)
        self._history: list[ChatMessage] = []
        # Loop detection: track recent tool call signatures
        self._loop_detector = LoopDetector()
        # Context tracking
        self._last_input_tokens: int = 0
        self._context_window: int = 0  # Resolved on first call
        # Live steering: async callable returning list[str] of messages that
        # arrived mid-response, to fold into the running conversation.
        self.pending_drain = None

    def _get_context_window(self) -> int:
        """Resolve the context window size for the current model."""
        if self._context_window > 0:
            return self._context_window
        model = settings.llm_model_name or ""
        self._context_window = model_registry.get_context_window(model)
        logger.info(
            f"[Context] Model '{model}' → context window {self._context_window:,} tokens"
        )
        return self._context_window

    def _estimate_tokens(self) -> int:
        """Estimate current context size in tokens.

        Uses the last API-reported input_tokens if available (most accurate).
        Falls back to character-based estimation (~4 chars per token).
        """
        if self._last_input_tokens > 0:
            return self._last_input_tokens

        # Rough estimate: sum all message content lengths / 4
        total_chars = 0
        image_tokens = 0
        for msg in self._history:
            if isinstance(msg.content, str) and msg.content:
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for c in msg.content:
                    # Don't count base64 image data as text — a vision image
                    # costs ~1.5k tokens regardless of byte size.
                    if isinstance(c, dict) and c.get("type") == "image":
                        image_tokens += 1500
                    else:
                        total_chars += len(str(c))
            if msg.tool_calls:
                total_chars += len(json.dumps(msg.tool_calls))
        return total_chars // 4 + image_tokens

    def _needs_compaction(self) -> bool:
        """Check if the conversation needs compaction."""
        if len(self._history) < 6:
            return False  # Too short to compact
        estimated = self._estimate_tokens()
        window = self._get_context_window()
        threshold = context_compressor.effective_threshold_tokens(window)
        if estimated >= threshold:
            logger.info(
                f"[Context] {estimated:,} tokens ≥ {threshold:,} threshold "
                f"(window {window:,}) — compaction needed"
            )
            return True
        return False

    async def _compact_history(self, message_id: str) -> None:
        """4-layer context compression pipeline.

        Layer 1–3 are deterministic and run first (fast, no LLM call).
        Layer 4 (LLM summarization) is only invoked if still over threshold.
        """
        provider = self._get_provider()
        window = self._get_context_window()

        await self.log_publisher.publish_chat(
            message_id, "text",
            {"text": "\n\n`[Kontext wird komprimiert...]`\n\n"},
        )

        # Layers 1–3: Snip → Microcompact → Collapse (deterministic)
        compressed, applied = context_compressor.compress_messages(
            self._history, window, target_pct=0.55
        )
        if applied:
            self._history = compressed
            logger.info(f"[Context] Deterministic layers {applied} applied")

        # Check if still over threshold after deterministic layers
        estimated = context_compressor.estimate_tokens(self._history)
        still_over = estimated > context_compressor.effective_threshold_tokens(window)

        if not still_over:
            self._last_input_tokens = 0
            return

        # Layer 4: LLM-based abstractive summarization (last resort)
        old_count = len(self._history)
        new_history = await context_compressor.summarize_messages(self._history, provider)
        if new_history:
            self._history = new_history
            self._last_input_tokens = 0
            logger.info(
                f"[Context] Layer 4 summarized {old_count} → {len(self._history)} msgs"
            )
        else:
            logger.warning("[Context] Layer 4 summarization failed; keeping current history")

    async def _get_catalog(self) -> list[dict]:
        """Full tool catalog (built-in + orchestrator API + MCP), cached. This is the
        SEARCHABLE set — not everything here is sent to the LLM."""
        if self._all_tools is not None:
            return self._all_tools
        catalog = list(TOOL_DEFINITIONS)
        try:
            mcp_tools = await self._mcp_client.discover_tools()
            if mcp_tools:
                catalog.extend(mcp_tools)
                logger.info(f"Discovered {len(mcp_tools)} MCP tools (catalog size {len(catalog)})")
        except Exception as e:
            logger.warning(f"MCP tool discovery failed: {e}")
        self._all_tools = catalog
        # Pre-activate the agent's integration MCP tools (M365/msgraph, Exchange, …)
        # so they are ALWAYS callable. Without this they're only reachable via
        # search_tools, and the model unreliably claims "no M365 tool available"
        # instead of searching (the "mal da / mal nicht" flakiness). Capped to
        # leave headroom for on-demand search_tools activations under the 128 limit.
        if not self._activated:
            mcp_names = [
                t["function"]["name"] for t in catalog
                if str(t.get("function", {}).get("name", "")).startswith("mcp_")
                and t["function"]["name"] not in CORE_TOOL_NAMES
            ]
            if mcp_names:
                self._activated = mcp_names[: max(1, MAX_ACTIVATED_TOOLS - 15)]
                logger.info(f"Pre-activated {len(self._activated)} integration MCP tools")
        return catalog

    async def _get_tools(self) -> list[dict] | None:
        """Tools actually SENT to the LLM this turn: the CORE set + search_tools +
        whatever was activated via search_tools so far. The rest of the catalog is
        reachable only by searching for it — that's what keeps us under the 128 cap."""
        if not settings.llm_tools_enabled:
            return None
        catalog = await self._get_catalog()
        active = set(self._activated)
        sent = [t for t in catalog if t["function"]["name"] in CORE_TOOL_NAMES]
        sent.append(SEARCH_TOOLS_DEF)
        sent += [t for t in catalog
                 if t["function"]["name"] in active and t["function"]["name"] not in CORE_TOOL_NAMES]
        return sent

    def _handle_search_tools(self, query: str) -> str:
        """Search the catalog and activate the best matches for the next turn."""
        matches = _search_catalog(self._all_tools or [], query, CORE_TOOL_NAMES | {"search_tools"})
        if not matches:
            return f"Keine passenden Tools für '{query}' gefunden. Versuch andere Stichwörter."
        lines = []
        for tool in matches:
            name = tool["function"]["name"]
            # Move-to-end (recency) + dedup
            if name in self._activated:
                self._activated.remove(name)
            self._activated.append(name)
            lines.append(f"- {name}: {(tool['function'].get('description') or '')[:160]}")
        # LRU cap so core + activated never approaches the 128 limit
        if len(self._activated) > MAX_ACTIVATED_TOOLS:
            self._activated = self._activated[-MAX_ACTIVATED_TOOLS:]
        return "Folgende Tools sind ab deinem nächsten Schritt aufrufbar:\n" + "\n".join(lines)

    def _get_provider(self) -> BaseLLMProvider:
        if not self._provider:
            self._provider = create_provider(
                provider_type=settings.llm_provider_type,
                api_endpoint=settings.llm_api_endpoint,
                api_key=settings.llm_api_key,
                model_name=settings.llm_model_name,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                thinking_mode=settings.llm_thinking_mode,
                api_version=settings.llm_api_version,
            )
        return self._provider

    async def handle_message(
        self,
        message_id: str,
        text: str,
        model: str | None = None,
        images: list[dict] | None = None,
    ) -> dict:
        """Process a chat message with the custom LLM provider.

        ``images`` is an optional list of ``{"media_type", "data"}`` dicts
        (base64) — e.g. a photo pasted in the Web UI or sent via Telegram.
        Multimodal models see them directly.
        """
        self.is_running = True
        start_time = time.time()
        provider = self._get_provider()

        # Build system message if this is the first message
        if not self._history:
            from app.runner_hooks import (
                MULTIMODAL_CAPABILITY_NOTE,
                get_skills_context,
                get_mounts_context,
                get_marketplace_skill_suggestions,
            )
            system_prompt = settings.llm_system_prompt or (
                "You are a helpful AI coding assistant running in a Docker container. "
                "Your workspace is at /workspace. Use the available tools to help the user."
            )
            system_prompt = system_prompt + MULTIMODAL_CAPABILITY_NOTE
            system_prompt = system_prompt + (
                "\n\n## Werkzeuge bei Bedarf nachladen\n"
                "Dir ist nur ein KERN-Satz an Werkzeugen direkt verfügbar. Für alles "
                "Weitere — Microsoft 365 (Mail, Kalender, Teams, OneDrive, Planner), "
                "Wissensdatenbank, Skills, weitere Integrationen — rufe ZUERST "
                "`search_tools` mit einer Beschreibung der gewünschten Aktion auf "
                "(z. B. 'Ordner in OneDrive anlegen'); die passenden Werkzeuge sind "
                "dann ab deinem nächsten Schritt aufrufbar."
            )
            # Host mounts / Second Brain awareness + marketplace skills — parity with
            # the task runtimes so chat agents also search the shared vault and skills.
            system_prompt = system_prompt + get_mounts_context()
            skills_ctx = get_skills_context()
            if skills_ctx:
                system_prompt = system_prompt + "\n" + skills_ctx
            marketplace = get_marketplace_skill_suggestions(text[:200])
            if marketplace:
                system_prompt = system_prompt + "\n" + marketplace
            self._history.append(ChatMessage(role="system", content=system_prompt))

        # Add user message to history (image-aware)
        self._history.append(multimodal.user_message(text, images))

        # Context compaction: if approaching context limit, summarize first
        if self._needs_compaction():
            await self._compact_history(message_id)

        # Reset loop detector for this message
        self._loop_detector.reset()

        tools = await self._get_tools()
        full_text = ""
        accumulated_tool_calls: list[dict] = []
        num_turns = 0
        max_turns = _max_turns()
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            while num_turns < max_turns:
                num_turns += 1
                # Re-fetch each turn so tools activated via search_tools on the
                # previous turn become callable now (lazy loading).
                tools = await self._get_tools()
                has_tool_calls = False
                turn_text = ""
                turn_tool_calls: list[dict] = []

                async for event in provider.stream_completion(self._history, tools):
                    if event.type == "text_delta":
                        turn_text += event.text
                        full_text += event.text
                        await self.log_publisher.publish_chat(
                            message_id, "text", {"text": event.text}
                        )

                    elif event.type == "tool_call":
                        has_tool_calls = True
                        turn_tool_calls.append({
                            "id": event.tool_id,
                            "name": event.tool_name,
                            "input": event.tool_input,
                        })
                        accumulated_tool_calls.append({
                            "tool": event.tool_name,
                            "input": json.dumps(event.tool_input)[:200],
                        })
                        await self.log_publisher.publish_chat(
                            message_id, "tool_call",
                            {
                                "tool_use_id": event.tool_id,
                                "tool": event.tool_name,
                                "input": event.tool_input,
                            },
                        )

                    elif event.type == "done":
                        # Track actual token usage from API for context monitoring
                        if event.input_tokens:
                            self._last_input_tokens = event.input_tokens
                        # Each turn is a separately-billed API call — sum every
                        # turn's tokens for the message's total cost.
                        total_input_tokens += event.input_tokens or 0
                        total_output_tokens += event.output_tokens or 0

                    elif event.type == "error":
                        await self.log_publisher.publish_chat(
                            message_id, "error", {"message": event.text}
                        )
                        self.is_running = False
                        result = {"status": "error", "error": event.text}
                        await self.log_publisher.publish_chat(message_id, "done", result)
                        return result

                # Add assistant response to history
                if turn_text and not turn_tool_calls:
                    self._history.append(ChatMessage(role="assistant", content=turn_text))
                elif turn_tool_calls:
                    tool_calls_content = [{
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])},
                    } for tc in turn_tool_calls]
                    self._history.append(ChatMessage(
                        role="assistant",
                        content=turn_text or None,
                        tool_calls=tool_calls_content,
                    ))

                if not has_tool_calls:
                    # Live steering: before finishing, fold in any messages
                    # that arrived while we were responding — same conversation.
                    if self.pending_drain is not None:
                        extra = await self.pending_drain()
                        if extra:
                            for t in extra:
                                self._history.append(ChatMessage(role="user", content=t))
                                full_text += f"\n\n[Neue Nachricht aufgenommen]\n"
                            await self.log_publisher.publish_chat(
                                message_id, "system",
                                {"message": f"{len(extra)} neue Nachricht(en) aufgenommen — wird mitverarbeitet."},
                            )
                            max_turns = num_turns + _max_turns()
                            continue
                    break

                # Loop detection: check for repetitive tool call patterns
                for tc in turn_tool_calls:
                    self._loop_detector.record(tc["name"], tc["input"])
                if self._loop_detector.is_looping():
                    loop_msg = (
                        "Loop detected: the same tool calls are repeating. "
                        "Stopping to prevent runaway execution."
                    )
                    logger.warning(f"[Chat] {loop_msg}")
                    full_text += f"\n\n[System: {loop_msg}]"
                    await self.log_publisher.publish_chat(
                        message_id, "text", {"text": f"\n\n[System: {loop_msg}]"}
                    )
                    break

                # Execute tool calls — parallelize read-only, serialize writes
                from app.tools.executor import _CACHEABLE_TOOLS
                _WRITE_TOOLS = {"write_file", "edit_file", "multi_edit", "bash"}

                results_map: dict[str, str] = {}
                # search_tools is handled in-handler (it owns the catalog + activation
                # set) and never reaches the executor — it loads tools for the next turn.
                for tc in turn_tool_calls:
                    if tc["name"] == "search_tools":
                        results_map[tc["id"]] = self._handle_search_tools(tc["input"].get("query", ""))
                _dispatch = [tc for tc in turn_tool_calls if tc["name"] != "search_tools"]

                read_only = [tc for tc in _dispatch if tc["name"] in _CACHEABLE_TOOLS]
                write_ops = [tc for tc in _dispatch if tc["name"] in _WRITE_TOOLS]
                other_ops = [tc for tc in _dispatch if tc not in read_only and tc not in write_ops]
                if read_only:
                    parallel_results = await asyncio.gather(
                        *[self._tool_executor.execute(tc["name"], tc["input"]) for tc in read_only],
                        return_exceptions=True,
                    )
                    for tc, res in zip(read_only, parallel_results):
                        results_map[tc["id"]] = str(res) if isinstance(res, Exception) else res

                for tc in other_ops + write_ops:
                    results_map[tc["id"]] = await self._tool_executor.execute(tc["name"], tc["input"])

                for tc in turn_tool_calls:
                    result_text = results_map[tc["id"]]
                    # present_image: stream the actual image to the chat UI
                    if tc["name"] == "present_image" and multimodal.is_image_result(result_text):
                        payload = multimodal.parse_image_result(result_text) or {}
                        if payload.get("data"):
                            await self.log_publisher.publish_chat(
                                message_id, "image",
                                {
                                    "media_type": payload.get("media_type", "image/png"),
                                    "data": payload["data"],
                                    "caption": payload.get("note", ""),
                                },
                            )
                    if tc["name"] == "present_file" and result_text.startswith("__AI_EMPLOYEE_PRESENT_FILE__"):
                        try:
                            payload = json.loads(result_text.removeprefix("__AI_EMPLOYEE_PRESENT_FILE__"))
                            await self.log_publisher.publish_chat(message_id, "file", payload)
                        except Exception:
                            pass
                    await self.log_publisher.publish_chat(
                        message_id, "tool_result",
                        {
                            "tool_use_id": tc["id"],
                            "content": "File presented to the user."
                            if result_text.startswith("__AI_EMPLOYEE_PRESENT_FILE__")
                            else multimodal.log_summary(result_text),
                        },
                    )
                    self._history.append(
                        multimodal.tool_message(result_text, tc["id"], tc["name"])
                    )

                # Mid-turn compaction: if a tool-heavy turn filled the context
                if self._needs_compaction():
                    await self._compact_history(message_id)

                # Live steering (mid-turn): fold in any messages that arrived
                # while the tools were running, so the agent picks up the new
                # info on its very NEXT step — not only at the end of the turn.
                # Drained AFTER compaction so fresh input is never summarized away.
                if self.pending_drain is not None:
                    extra = await self.pending_drain()
                    if extra:
                        for t in extra:
                            self._history.append(ChatMessage(role="user", content=t))
                            full_text += "\n\n[Neue Nachricht aufgenommen]\n"
                        await self.log_publisher.publish_chat(
                            message_id, "system",
                            {"message": f"{len(extra)} neue Nachricht(en) aufgenommen — wird sofort mitverarbeitet."},
                        )
                        # New input extends the work budget for this message.
                        max_turns = num_turns + _max_turns()

        except Exception as e:
            logger.exception(f"LLM Chat error: {e}")
            await self.log_publisher.publish_chat(
                message_id, "error", {"message": str(e)}
            )
            self.is_running = False
            result = {"status": "error", "error": str(e)}
            await self.log_publisher.publish_chat(message_id, "done", result)
            return result

        duration_ms = int((time.time() - start_time) * 1000)
        from app.llm_runner import _estimate_cost
        result = {
            "status": "completed",
            "text": full_text,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "cost_usd": _estimate_cost(
                settings.llm_model_name, total_input_tokens, total_output_tokens
            ),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "tool_calls": accumulated_tool_calls or None,
        }

        self.is_running = False
        await self.log_publisher.publish_chat(message_id, "done", result)
        return result

    async def stop_current(self) -> None:
        """Stop the currently running request."""
        self.is_running = False
        if self._provider:
            await self._provider.close()
            self._provider = None

    async def reset_session(self) -> None:
        """Reset conversation history."""
        self._history.clear()
        self._loop_detector.reset()
        self._last_input_tokens = 0
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Chat session reset"}
        )
