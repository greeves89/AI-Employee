"""Anthropic provider - uses the Messages API with streaming."""

import json
import logging
from typing import AsyncIterator

import httpx

from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Provider for the Anthropic Messages API (claude models via direct API)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def stream_completion(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream a chat completion via Anthropic Messages API."""
        url = f"{self.api_endpoint}/messages"

        # Separate system message from conversation
        system_text = ""
        conv_messages = []
        for msg in messages:
            if msg.role == "system":
                system_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            elif msg.role == "tool":
                conv_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                    }],
                })
            else:
                content = msg.content if isinstance(msg.content, str) else msg.content
                conv_messages.append({"role": msg.role, "content": content})

        body: dict = {
            "model": self.model_name,
            "messages": conv_messages,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if system_text:
            body["system"] = system_text

        # Convert OpenAI tool format to Anthropic format
        if tools:
            anthropic_tools = []
            for t in tools:
                func = t.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            body["tools"] = anthropic_tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        input_tokens = 0
        output_tokens = 0
        current_tool_id = ""
        current_tool_name = ""
        current_tool_json = ""

        try:
            async with self._client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    yield LLMEvent(type="error", text=f"API error {response.status_code}: {error_body.decode()}")
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    if event_type == "message_start":
                        usage = event.get("message", {}).get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)

                    elif event_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool_id = block.get("id", "")
                            current_tool_name = block.get("name", "")
                            current_tool_json = ""

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield LLMEvent(type="text_delta", text=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            current_tool_json += delta.get("partial_json", "")

                    elif event_type == "content_block_stop":
                        if current_tool_name:
                            try:
                                tool_input = json.loads(current_tool_json) if current_tool_json else {}
                            except json.JSONDecodeError:
                                tool_input = {"raw": current_tool_json}
                            yield LLMEvent(
                                type="tool_call",
                                tool_id=current_tool_id,
                                tool_name=current_tool_name,
                                tool_input=tool_input,
                            )
                            current_tool_id = ""
                            current_tool_name = ""
                            current_tool_json = ""

                    elif event_type == "message_delta":
                        usage = event.get("usage", {})
                        output_tokens = usage.get("output_tokens", output_tokens)

                    elif event_type == "message_stop":
                        pass

        except httpx.ConnectError as e:
            yield LLMEvent(type="error", text=f"Connection failed: {e}")
            return
        except httpx.ReadTimeout:
            yield LLMEvent(type="error", text="Request timed out")
            return
        except Exception as e:
            yield LLMEvent(type="error", text=f"Unexpected error: {e}")
            return

        yield LLMEvent(type="done", input_tokens=input_tokens, output_tokens=output_tokens)

    async def close(self) -> None:
        await self._client.aclose()
