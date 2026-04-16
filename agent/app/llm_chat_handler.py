"""LLM Chat Handler - interactive chat sessions using custom LLM providers."""

import asyncio
import json
import logging
import time

from app.config import settings
from app.log_publisher import LogPublisher
from app.providers import create_provider
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent
from app.tools.definitions import TOOL_DEFINITIONS
from app.tools.executor import ToolExecutor
from app.tools.mcp_client import MCPHTTPClient

logger = logging.getLogger(__name__)

MAX_TURNS_PER_MESSAGE = 20  # Max tool-use loops per chat message
LOOP_DETECTION_WINDOW = 6   # Consecutive identical tool calls to detect as loop
COMPACTION_THRESHOLD = 0.75  # Trigger compaction at 75% of context window

# Context window sizes per model (tokens).
# Claude Code CLI handles its own compaction — this is for custom LLM mode only.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "gpt-5": 1_000_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    # Anthropic
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    # Google
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
    "gemini-1.5-flash": 1_000_000,
    # Local / open models (conservative defaults)
    "llama": 8_192,
    "mistral": 32_768,
    "codestral": 32_768,
    "deepseek": 128_000,
    "qwen": 128_000,
}

DEFAULT_CONTEXT_WINDOW = 128_000  # Fallback if model not in table

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


class LLMChatHandler:
    """Handles interactive chat sessions using custom LLM providers.

    Same interface as ChatHandler — publishes identical events via
    LogPublisher.publish_chat() so the frontend sees no difference.

    Context management: tracks token usage vs model context window.
    When usage exceeds COMPACTION_THRESHOLD (75%), triggers an LLM-based
    summarization of the conversation, replaces history with the summary,
    and continues seamlessly.
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.is_running = False
        self._provider: BaseLLMProvider | None = None
        self._tool_executor = ToolExecutor()
        self._mcp_client = MCPHTTPClient()
        self._all_tools: list[dict] | None = None
        # Conversation history (in-memory, replaces --resume)
        self._history: list[ChatMessage] = []
        # Loop detection: track recent tool call signatures
        self._recent_tool_sigs: list[str] = []
        # Context tracking
        self._last_input_tokens: int = 0
        self._context_window: int = 0  # Resolved on first call

    def _get_context_window(self) -> int:
        """Resolve the context window size for the current model."""
        if self._context_window > 0:
            return self._context_window

        model = (settings.llm_model_name or "").lower()

        # Exact match
        for key, size in MODEL_CONTEXT_WINDOWS.items():
            if key in model:
                self._context_window = size
                logger.info(f"[Context] Model '{model}' → context window {size:,} tokens")
                return size

        self._context_window = DEFAULT_CONTEXT_WINDOW
        logger.info(
            f"[Context] Model '{model}' not in table, "
            f"using default {DEFAULT_CONTEXT_WINDOW:,} tokens"
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
        for msg in self._history:
            if isinstance(msg.content, str) and msg.content:
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                total_chars += sum(len(str(c)) for c in msg.content)
            if msg.tool_calls:
                total_chars += len(json.dumps(msg.tool_calls))
        return total_chars // 4

    def _needs_compaction(self) -> bool:
        """Check if the conversation needs compaction."""
        if len(self._history) < 6:
            return False  # Too short to compact
        estimated = self._estimate_tokens()
        window = self._get_context_window()
        usage_pct = estimated / window
        if usage_pct >= COMPACTION_THRESHOLD:
            logger.info(
                f"[Context] {usage_pct:.0%} used "
                f"({estimated:,}/{window:,} tokens) — compaction needed"
            )
            return True
        return False

    async def _compact_history(self, message_id: str) -> None:
        """Ask the LLM to summarize the conversation, then replace history.

        Flow:
        1. Send full history + compaction prompt to LLM
        2. LLM returns a summary of everything so far
        3. Replace history with: [system_prompt, summary, last_user_message]
        """
        provider = self._get_provider()

        # Notify user that compaction is happening
        await self.log_publisher.publish_chat(
            message_id, "text",
            {"text": "\n\n`[Kontext wird komprimiert...]`\n\n"},
        )

        # Build compaction request: full history + summary instruction
        compact_messages = list(self._history) + [
            ChatMessage(role="user", content=COMPACTION_PROMPT)
        ]

        # Call LLM without tools (pure text summary)
        summary_text = ""
        try:
            async for event in provider.stream_completion(compact_messages, tools=None):
                if event.type == "text_delta":
                    summary_text += event.text
                elif event.type == "done":
                    # Update token tracking from compaction call
                    if event.input_tokens:
                        self._last_input_tokens = 0  # Reset — new context is tiny
                elif event.type == "error":
                    logger.error(f"[Context] Compaction LLM call failed: {event.text}")
                    return  # Keep old history if compaction fails
        except Exception as e:
            logger.error(f"[Context] Compaction failed: {e}")
            return  # Keep old history on error

        if not summary_text.strip():
            logger.warning("[Context] Compaction returned empty summary, skipping")
            return

        # Rebuild history: system prompt + summary + keep last user message
        system_msg = None
        if self._history and self._history[0].role == "system":
            system_msg = self._history[0]

        last_user = None
        for msg in reversed(self._history):
            if msg.role == "user":
                last_user = msg
                break

        new_history: list[ChatMessage] = []
        if system_msg:
            new_history.append(system_msg)

        # Insert summary as an assistant message so the LLM knows the context
        new_history.append(ChatMessage(
            role="assistant",
            content=(
                f"[Conversation Summary — compacted to save context]\n\n"
                f"{summary_text}"
            ),
        ))

        if last_user:
            new_history.append(last_user)

        old_count = len(self._history)
        self._history = new_history
        self._last_input_tokens = 0  # Reset — will be re-measured on next call

        logger.info(
            f"[Context] Compacted {old_count} messages → {len(self._history)} messages. "
            f"Summary: {len(summary_text)} chars"
        )

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

    async def handle_message(
        self, message_id: str, text: str, model: str | None = None
    ) -> dict:
        """Process a chat message with the custom LLM provider."""
        self.is_running = True
        start_time = time.time()
        provider = self._get_provider()

        # Build system message if this is the first message
        if not self._history:
            system_prompt = settings.llm_system_prompt or (
                "You are a helpful AI coding assistant running in a Docker container. "
                "Your workspace is at /workspace. Use the available tools to help the user."
            )
            self._history.append(ChatMessage(role="system", content=system_prompt))

        # Add user message to history
        self._history.append(ChatMessage(role="user", content=text))

        # Context compaction: if approaching context limit, summarize first
        if self._needs_compaction():
            await self._compact_history(message_id)

        # Reset loop detector for this message
        self._recent_tool_sigs.clear()

        tools = await self._get_tools()
        full_text = ""
        accumulated_tool_calls: list[dict] = []
        num_turns = 0

        try:
            while num_turns < MAX_TURNS_PER_MESSAGE:
                num_turns += 1
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
                    break

                # Loop detection: check for repetitive tool call patterns
                for tc in turn_tool_calls:
                    sig = f"{tc['name']}:{json.dumps(tc['input'], sort_keys=True)}"
                    self._recent_tool_sigs.append(sig)
                if self._detect_loop():
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

                read_only = [tc for tc in turn_tool_calls if tc["name"] in _CACHEABLE_TOOLS]
                write_ops = [tc for tc in turn_tool_calls if tc["name"] in _WRITE_TOOLS]
                other_ops = [tc for tc in turn_tool_calls if tc not in read_only and tc not in write_ops]

                results_map: dict[str, str] = {}
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
                    await self.log_publisher.publish_chat(
                        message_id, "tool_result",
                        {"tool_use_id": tc["id"], "content": result_text[:2000]},
                    )
                    self._history.append(ChatMessage(
                        role="tool",
                        content=result_text,
                        tool_call_id=tc["id"],
                        name=tc["name"],
                    ))

                # Mid-turn compaction: if a tool-heavy turn filled the context
                if self._needs_compaction():
                    await self._compact_history(message_id)

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
        result = {
            "status": "completed",
            "text": full_text,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "cost_usd": 0,
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

    def _detect_loop(self) -> bool:
        """Detect repetitive tool call patterns.

        Returns True if the last LOOP_DETECTION_WINDOW tool calls all have
        the same signature (same tool name + same arguments).
        """
        if len(self._recent_tool_sigs) < LOOP_DETECTION_WINDOW:
            return False
        window = self._recent_tool_sigs[-LOOP_DETECTION_WINDOW:]
        return len(set(window)) == 1

    async def reset_session(self) -> None:
        """Reset conversation history."""
        self._history.clear()
        self._recent_tool_sigs.clear()
        self._last_input_tokens = 0
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Chat session reset"}
        )
