"""LLM Runner - executes tasks using custom LLM providers with an agentic tool loop."""

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
from app.runner_hooks import (
    MULTIMODAL_CAPABILITY_NOTE,
    SELF_IMPROVEMENT_SUFFIX,
    TASK_STARTUP_PREFIX,
    get_approval_rules_prefix,
    get_improvement_context,
    get_marketplace_skill_suggestions,
    get_memory_preload,
    get_mounts_context,
    get_skill_preload,
    get_skills_context,
)
from app.tools.definitions import TOOL_DEFINITIONS
from app.tools.executor import ToolExecutor
from app.tools.mcp_client import MCPHTTPClient
# Lazy tool loading (shared with the chat handler) — keeps the per-request tool
# array under the 128-tool cap that OpenAI/Azure enforce.
from app.llm_chat_handler import (
    CORE_TOOL_NAMES, SEARCH_TOOLS_DEF, MAX_ACTIVATED_TOOLS, _search_catalog,
)

logger = logging.getLogger(__name__)

# Safety upper-bound; actual cap comes from settings.max_turns
MAX_TURNS_HARD_CAP = 200

# Context compression triggers at context_compressor.effective_threshold_tokens
# (min of 75% of the model window or an absolute token budget).

TOOL_USAGE_RULES = """
TOOL USAGE RULES (follow strictly):
- To modify an existing file, use `edit_file` or `multi_edit` — NEVER use `write_file` unless creating a brand-new file. Full overwrites waste tokens and corrupt large files.
- To search code, use `grep` (content) or `glob` (filenames). Do NOT shell out to bash for grep/find.
- To fetch docs or URLs, use `web_fetch`. Do NOT use bash(curl) for web content.
- To inspect git state, use `git_status` / `git_diff`, not `bash("git ...")`.
- `bash` is for building, testing, installing packages, running scripts — not for file I/O or search.

WORKFLOW:
1. Explore first (glob / grep / read_file / list_files) before editing.
2. For edits, include enough surrounding context in `old_string` so it's unique.
3. After writing code, run the build/tests via `bash` to validate. Never claim success on unverified code.
"""

def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost based on token counts (delegates to the model registry).

    Kept as a module-level function because llm_chat_handler imports it.
    """
    return model_registry.estimate_cost(model, input_tokens, output_tokens)


class LLMRunner:
    """Executes tasks via custom LLM providers with tool-use support.

    Same interface as AgentRunner.execute_task() - publishes identical
    events via LogPublisher so the frontend sees no difference.
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.is_running = False
        self._provider: BaseLLMProvider | None = None
        self._tool_executor = ToolExecutor()
        self._mcp_client = MCPHTTPClient()
        # The executor must call MCP tools on the SAME client that ran discovery —
        # otherwise its lazily-created client has an empty registry and every MCP
        # tool call fails with "Unknown MCP tool".
        self._tool_executor._mcp_client = self._mcp_client
        self._all_tools: list[dict] | None = None
        # Lazy tool loading: tools activated on demand via search_tools (LRU-capped).
        # Only CORE + search_tools + these are sent per request → under the 128 cap.
        self._activated: list[str] = []
        self._context_window: int = 0

    def _get_context_window(self) -> int:
        """Resolve the context window size for the current model."""
        if self._context_window > 0:
            return self._context_window
        self._context_window = model_registry.get_context_window(settings.llm_model_name or "")
        return self._context_window

    @staticmethod
    def _compliance_gaps(tools_called: set[str], lightweight: bool) -> list[str]:
        """Mandatory closing steps the agent skipped (enforced, not prompt-only).

        Lightweight (chat-style) tasks don't require task reflection.
        """
        gaps: list[str] = []
        if not lightweight and "rate_task" not in tools_called:
            gaps.append("call rate_task to record this task's quality")
        if "skill_install" in tools_called and "skill_rate" not in tools_called:
            gaps.append("call skill_rate for the marketplace skill you installed")
        return gaps

    async def _get_catalog(self) -> list[dict]:
        """Full tool catalog (built-in + MCP), cached + searchable by search_tools."""
        if self._all_tools is not None:
            return self._all_tools
        self._all_tools = list(TOOL_DEFINITIONS)
        try:
            mcp_tools = await self._mcp_client.discover_tools()
            if mcp_tools:
                self._all_tools.extend(mcp_tools)
                logger.info(f"Discovered {len(mcp_tools)} MCP tools")
        except Exception as e:
            logger.warning(f"MCP tool discovery failed: {e}")
        # Pre-activate integration MCP tools (M365/msgraph, Exchange, …) so they are
        # always callable instead of only via search_tools — same reliability fix as
        # the chat handler. Capped to leave headroom under the 128-tool limit.
        if not self._activated:
            mcp_names = [
                t["function"]["name"] for t in self._all_tools
                if str(t.get("function", {}).get("name", "")).startswith("mcp_")
                and t["function"]["name"] not in CORE_TOOL_NAMES
            ]
            if mcp_names:
                self._activated = mcp_names[: max(1, MAX_ACTIVATED_TOOLS - 15)]
                logger.info(f"Pre-activated {len(self._activated)} integration MCP tools")
        return self._all_tools

    async def _get_tools(self) -> list[dict] | None:
        """Tools actually SENT to the LLM this turn: CORE set + search_tools +
        on-demand activated tools. The full catalog (which can exceed 128) is
        reachable only via search_tools — that's what keeps each request under the
        OpenAI/Azure 128-tool cap. Identical mechanism to the chat handler."""
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
            if name in self._activated:
                self._activated.remove(name)
            self._activated.append(name)
            lines.append(f"- {name}: {(tool['function'].get('description') or '')[:160]}")
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

    async def execute_task(
        self, task_id: str, prompt: str, model: str | None = None,
        lightweight: bool = False,
    ) -> dict:
        """Execute a task with the custom LLM provider.

        Args:
            lightweight: If True, skip startup checks and self-improvement.
                        Use for chat/telegram messages where speed matters.
        """
        self.is_running = True
        start_time = time.time()
        provider = self._get_provider()

        # Build system prompt
        base_system = settings.llm_system_prompt or (
            "You are a helpful AI coding assistant running in a Docker container. "
            "Your workspace is at /workspace. Use the available tools to complete tasks."
        )
        base_system = base_system + MULTIMODAL_CAPABILITY_NOTE

        skills_ctx = get_skills_context()
        # Host mounts / Second Brain awareness — custom_llm builds its own system
        # prompt and never reads the instruction file, so inject it here (parity
        # with the CLI runtimes, which get it via the bundle / CLAUDE.md).
        mounts_ctx = get_mounts_context()

        if lightweight:
            from app.runner_hooks import CHAT_STARTUP_PREFIX
            system_prompt = base_system + "\n\n" + TOOL_USAGE_RULES + mounts_ctx
            if skills_ctx:
                system_prompt += "\n" + skills_ctx
            marketplace_suggestions = get_marketplace_skill_suggestions(prompt[:200])
            enhanced_prompt = CHAT_STARTUP_PREFIX + marketplace_suggestions + prompt
        else:
            memory_preload = get_memory_preload()
            approval_rules = get_approval_rules_prefix()
            improvement_ctx = get_improvement_context()
            system_prompt = (
                base_system
                + "\n\n"
                + TOOL_USAGE_RULES
                + approval_rules
                + mounts_ctx
                + memory_preload
                + improvement_ctx
            )
            if skills_ctx:
                system_prompt += "\n" + skills_ctx
            marketplace_suggestions = get_marketplace_skill_suggestions(prompt[:200])
            enhanced_prompt = TASK_STARTUP_PREFIX + marketplace_suggestions + prompt + SELF_IMPROVEMENT_SUFFIX

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=enhanced_prompt),
        ]

        tools = await self._get_tools()
        total_input_tokens = 0
        total_output_tokens = 0
        num_turns = 0
        full_text = ""
        accumulated_tool_calls: list[dict] = []
        tools_called: set[str] = set()      # every tool name used this task
        compliance_nudges = 0               # bounded: nudge missing closing steps once
        loop_detector = LoopDetector()

        # Cap turns at settings.max_turns, but enforce hard ceiling
        max_turns = min(settings.max_turns, MAX_TURNS_HARD_CAP)

        await self.log_publisher.publish(
            task_id, "system",
            {"message": f"Starting task with {settings.llm_provider_type}/{settings.llm_model_name}"},
        )

        try:
            while num_turns < max_turns:
                num_turns += 1
                has_tool_calls = False
                turn_text = ""
                turn_tool_calls: list[dict] = []
                tools = await self._get_tools()  # re-fetch each turn: picks up search_tools activations

                async for event in provider.stream_completion(messages, tools):
                    if event.type == "text_delta":
                        turn_text += event.text
                        full_text += event.text
                        await self.log_publisher.publish(
                            task_id, "text", {"text": event.text}
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
                        await self.log_publisher.publish(
                            task_id, "tool_call",
                            {
                                "tool_use_id": event.tool_id,
                                "tool": event.tool_name,
                                "input": event.tool_input,
                            },
                        )

                    elif event.type == "done":
                        total_input_tokens += event.input_tokens
                        total_output_tokens += event.output_tokens

                    elif event.type == "error":
                        await self.log_publisher.publish(
                            task_id, "error", {"message": event.text}
                        )
                        self.is_running = False
                        return {
                            "status": "error",
                            "error": event.text,
                            "num_turns": num_turns,
                        }

                # Add assistant message to history
                if turn_text and not turn_tool_calls:
                    messages.append(ChatMessage(role="assistant", content=turn_text))
                elif turn_tool_calls:
                    # Build assistant message with tool_calls for OpenAI format
                    tool_calls_content = []
                    for tc in turn_tool_calls:
                        tool_calls_content.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["input"]),
                            },
                        })
                    messages.append(ChatMessage(
                        role="assistant",
                        content=turn_text or None,
                        tool_calls=tool_calls_content,
                    ))

                tools_called.update(tc["name"] for tc in turn_tool_calls)

                if not has_tool_calls:
                    # Compliance gate: weak models tend to skip the mandatory
                    # closing steps the prompt asks for (rate_task, skill_rate).
                    # Enforce them in code — nudge once, then let the task end.
                    missing = self._compliance_gaps(tools_called, lightweight)
                    if missing and compliance_nudges < 1:
                        compliance_nudges += 1
                        messages.append(ChatMessage(
                            role="user",
                            content=(
                                "[SYSTEM] You are about to finish, but you still MUST: "
                                + "; ".join(missing)
                                + ". Do this now using the tools, then give your final answer."
                            ),
                        ))
                        await self.log_publisher.publish(
                            task_id, "system",
                            {"message": f"Compliance nudge — missing: {', '.join(missing)}"},
                        )
                        max_turns = num_turns + 4  # bounded room to comply
                        continue
                    # No tool calls = response is complete
                    break

                # Loop detection — stop if the same tool call keeps repeating
                for tc in turn_tool_calls:
                    loop_detector.record(tc["name"], tc["input"])
                if loop_detector.is_looping():
                    loop_msg = (
                        "Loop detected: the same tool call is repeating. "
                        "Stopping to prevent runaway execution."
                    )
                    logger.warning(f"[Runner] {loop_msg}")
                    full_text += f"\n\n[System: {loop_msg}]"
                    await self.log_publisher.publish(
                        task_id, "system", {"message": loop_msg}
                    )
                    break

                # search_tools is handled in-runner (it owns the catalog + activation),
                # NOT dispatched to the executor.
                results_map: dict[str, str] = {}
                for tc in turn_tool_calls:
                    if tc["name"] == "search_tools":
                        results_map[tc["id"]] = self._handle_search_tools(
                            (tc.get("input") or {}).get("query", "")
                        )
                _dispatch = [tc for tc in turn_tool_calls if tc["name"] != "search_tools"]

                # Execute tool calls — parallelize concurrent-safe, serialize writes
                from app.tools.executor import CONCURRENT_SAFE_TOOLS
                _WRITE_TOOLS = {"write_file", "edit_file", "multi_edit", "bash"}

                concurrent = [tc for tc in _dispatch if tc["name"] in CONCURRENT_SAFE_TOOLS]
                write_ops = [tc for tc in _dispatch if tc["name"] in _WRITE_TOOLS]
                other_ops = [tc for tc in _dispatch if tc not in concurrent and tc not in write_ops]

                # Run concurrent-safe tools in parallel (semaphore-capped inside executor)
                if concurrent:
                    parallel_results = await asyncio.gather(
                        *[self._tool_executor.execute(tc["name"], tc["input"]) for tc in concurrent],
                        return_exceptions=True,
                    )
                    for tc, res in zip(concurrent, parallel_results):
                        results_map[tc["id"]] = str(res) if isinstance(res, Exception) else res

                # Run other/write tools sequentially
                for tc in other_ops + write_ops:
                    results_map[tc["id"]] = await self._tool_executor.execute(tc["name"], tc["input"])

                # Add results in original order
                for tc in turn_tool_calls:
                    result = results_map[tc["id"]]
                    await self.log_publisher.publish(
                        task_id, "tool_result",
                        {"tool_use_id": tc["id"], "content": multimodal.log_summary(result)},
                    )
                    messages.append(
                        multimodal.tool_message(result, tc["id"], tc["name"])
                    )

                # Context compression: check after each tool round
                window = self._get_context_window()
                threshold = context_compressor.effective_threshold_tokens(window)
                if total_input_tokens > 0:
                    current_tokens = total_input_tokens
                else:
                    current_tokens = context_compressor.estimate_tokens(messages)

                if current_tokens >= threshold:
                    await self.log_publisher.publish(
                        task_id, "system", {"message": "[Context compressing...]"}
                    )
                    # Layers 1–3: deterministic, fast
                    compressed, applied = context_compressor.compress_messages(
                        messages, window, target_pct=0.55
                    )
                    if applied:
                        messages = compressed
                        logger.info(f"[Context] Runner layers {applied} applied")
                        total_input_tokens = 0  # Re-measure on next API call

                    # Layer 4: rolling summary if still over threshold
                    estimated = context_compressor.estimate_tokens(messages)
                    if estimated > threshold:
                        new_msgs = await context_compressor.summarize_messages(
                            messages, provider
                        )
                        if new_msgs:
                            logger.info(
                                f"[Context] Runner L4 summarized "
                                f"{len(messages)} → {len(new_msgs)} msgs"
                            )
                            messages = new_msgs
                            total_input_tokens = 0

        except Exception as e:
            logger.exception(f"LLM Runner error: {e}")
            await self.log_publisher.publish(task_id, "error", {"message": str(e)})
            self.is_running = False
            return {"status": "error", "error": str(e), "num_turns": num_turns}

        duration_ms = int((time.time() - start_time) * 1000)
        cost_usd = _estimate_cost(
            settings.llm_model_name, total_input_tokens, total_output_tokens
        )

        # Publish result event
        await self.log_publisher.publish(
            task_id, "result",
            {
                "cost_usd": cost_usd,
                "duration_ms": duration_ms,
                "num_turns": num_turns,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
        )

        self.is_running = False
        return {
            "status": "completed",
            "result": full_text,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "cost_usd": cost_usd,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "tool_calls": accumulated_tool_calls or None,
        }

    async def interrupt(self) -> None:
        """Interrupt the current task."""
        self.is_running = False
        if self._provider:
            await self._provider.close()
            self._provider = None
