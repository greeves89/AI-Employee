"""LLM Chat Handler - interactive chat sessions using custom LLM providers."""

import json
import logging
import time

from app.config import settings
from app.log_publisher import LogPublisher
from app.providers import create_provider
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent
from app.tools.definitions import TOOL_DEFINITIONS
from app.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

MAX_TURNS_PER_MESSAGE = 20  # Max tool-use loops per chat message


class LLMChatHandler:
    """Handles interactive chat sessions using custom LLM providers.

    Same interface as ChatHandler - publishes identical events via
    LogPublisher.publish_chat() so the frontend sees no difference.
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.is_running = False
        self._provider: BaseLLMProvider | None = None
        self._tool_executor = ToolExecutor()
        # Conversation history (in-memory, replaces --resume)
        self._history: list[ChatMessage] = []

    def _get_provider(self) -> BaseLLMProvider:
        if not self._provider:
            self._provider = create_provider(
                provider_type=settings.llm_provider_type,
                api_endpoint=settings.llm_api_endpoint,
                api_key=settings.llm_api_key,
                model_name=settings.llm_model_name,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
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

        tools = TOOL_DEFINITIONS if settings.llm_tools_enabled else None
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

                # Execute tool calls and add results
                for tc in turn_tool_calls:
                    result_text = await self._tool_executor.execute(tc["name"], tc["input"])
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

    async def reset_session(self) -> None:
        """Reset conversation history."""
        self._history.clear()
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Chat session reset"}
        )
