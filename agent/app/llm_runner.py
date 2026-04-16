"""LLM Runner - executes tasks using custom LLM providers with an agentic tool loop."""

import asyncio
import json
import logging
import time

from app.config import settings
from app.log_publisher import LogPublisher
from app.providers import create_provider
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent
from app.runner_hooks import (
    SELF_IMPROVEMENT_SUFFIX,
    TASK_STARTUP_PREFIX,
    get_approval_rules_prefix,
    get_improvement_context,
    get_memory_preload,
    get_skill_preload,
)
from app.tools.definitions import TOOL_DEFINITIONS
from app.tools.executor import ToolExecutor
from app.tools.mcp_client import MCPHTTPClient

logger = logging.getLogger(__name__)

# Safety upper-bound; actual cap comes from settings.max_turns
MAX_TURNS_HARD_CAP = 200

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

# Token pricing per 1M tokens (USD). Add entries as you use new models.
# Format: (input_price_per_1m, output_price_per_1m)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-5": (1.25, 10.00),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o3-mini": (1.10, 4.40),
    # Anthropic
    "claude-opus-4-6": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # Google
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost based on token counts."""
    # Try exact match, then prefix match (e.g. "gpt-4o-2024-08-06" -> "gpt-4o")
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        for known_model, prices in MODEL_PRICING.items():
            if model.startswith(known_model):
                pricing = prices
                break
    if not pricing:
        return 0.0
    in_price, out_price = pricing
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


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
        self._all_tools: list[dict] | None = None

    async def _get_tools(self) -> list[dict] | None:
        """Get combined built-in + MCP tool definitions."""
        if not settings.llm_tools_enabled:
            return None
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
        return self._all_tools

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

        if lightweight:
            from app.runner_hooks import CHAT_STARTUP_PREFIX
            system_prompt = base_system + "\n\n" + TOOL_USAGE_RULES
            enhanced_prompt = CHAT_STARTUP_PREFIX + prompt
        else:
            memory_preload = get_memory_preload()
            approval_rules = get_approval_rules_prefix()
            improvement_ctx = get_improvement_context()
            system_prompt = (
                base_system
                + "\n\n"
                + TOOL_USAGE_RULES
                + approval_rules
                + memory_preload
                + improvement_ctx
            )
            enhanced_prompt = TASK_STARTUP_PREFIX + prompt + SELF_IMPROVEMENT_SUFFIX

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

                if not has_tool_calls:
                    # No tool calls = response is complete
                    break

                # Execute tool calls — parallelize read-only, serialize writes
                from app.tools.executor import _CACHEABLE_TOOLS
                _WRITE_TOOLS = {"write_file", "edit_file", "multi_edit", "bash"}

                read_only = [tc for tc in turn_tool_calls if tc["name"] in _CACHEABLE_TOOLS]
                write_ops = [tc for tc in turn_tool_calls if tc["name"] in _WRITE_TOOLS]
                other_ops = [tc for tc in turn_tool_calls if tc not in read_only and tc not in write_ops]

                # Run read-only tools in parallel
                results_map: dict[str, str] = {}
                if read_only:
                    parallel_results = await asyncio.gather(
                        *[self._tool_executor.execute(tc["name"], tc["input"]) for tc in read_only],
                        return_exceptions=True,
                    )
                    for tc, res in zip(read_only, parallel_results):
                        results_map[tc["id"]] = str(res) if isinstance(res, Exception) else res

                # Run other/write tools sequentially
                for tc in other_ops + write_ops:
                    results_map[tc["id"]] = await self._tool_executor.execute(tc["name"], tc["input"])

                # Add results in original order
                for tc in turn_tool_calls:
                    result = results_map[tc["id"]]
                    await self.log_publisher.publish(
                        task_id, "tool_result",
                        {"tool_use_id": tc["id"], "content": result[:2000]},
                    )
                    messages.append(ChatMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    ))

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
