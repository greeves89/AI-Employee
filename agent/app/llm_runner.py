"""LLM Runner - executes tasks using custom LLM providers with an agentic tool loop."""

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

MAX_TURNS = 50  # Safety limit for agentic loops


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

    async def execute_task(
        self, task_id: str, prompt: str, model: str | None = None
    ) -> dict:
        """Execute a task with the custom LLM provider."""
        self.is_running = True
        start_time = time.time()
        provider = self._get_provider()

        # Build system prompt
        system_prompt = settings.llm_system_prompt or (
            "You are a helpful AI coding assistant running in a Docker container. "
            "Your workspace is at /workspace. Use the available tools to complete tasks."
        )

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompt),
        ]

        tools = TOOL_DEFINITIONS if settings.llm_tools_enabled else None
        total_input_tokens = 0
        total_output_tokens = 0
        num_turns = 0
        full_text = ""
        accumulated_tool_calls: list[dict] = []

        await self.log_publisher.publish(
            task_id, "system",
            {"message": f"Starting task with {settings.llm_provider_type}/{settings.llm_model_name}"},
        )

        try:
            while num_turns < MAX_TURNS:
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

                # Execute tool calls and add results to messages
                for tc in turn_tool_calls:
                    result = await self._tool_executor.execute(tc["name"], tc["input"])
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

        # Publish result event
        await self.log_publisher.publish(
            task_id, "result",
            {
                "cost_usd": 0,  # Token-based cost tracking later
                "duration_ms": duration_ms,
                "num_turns": num_turns,
            },
        )

        self.is_running = False
        return {
            "status": "completed",
            "result": full_text,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "cost_usd": 0,
            "tool_calls": accumulated_tool_calls or None,
        }

    async def interrupt(self) -> None:
        """Interrupt the current task."""
        self.is_running = False
        if self._provider:
            await self._provider.close()
            self._provider = None
