"""OpenAI-compatible provider (covers OpenAI, Azure, Together, Groq, vLLM, Ollama).

Supports two endpoint formats:
- Chat completions (default): /chat/completions  (messages-based)
- Legacy completions: /completions  (prompt-based, for Codex etc.)

The format is auto-detected from the endpoint URL:
- If the endpoint already ends with /completions (not /chat/completions) → legacy
- Otherwise → chat completions (appends /chat/completions if needed)
"""

import json
import logging
from typing import AsyncIterator

import httpx

from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible APIs with streaming."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    def _resolve_url(self) -> tuple[str, bool]:
        """Determine the full URL and whether to use legacy completions format.

        Returns (url, is_legacy).
        """
        ep = self.api_endpoint.rstrip("/")

        # User specified the full path already
        if ep.endswith("/chat/completions"):
            return ep, False
        if ep.endswith("/completions"):
            return ep, True
        if ep.endswith("/responses"):
            return ep, False  # Responses API uses messages format

        # Base URL only → default to chat completions
        return f"{ep}/chat/completions", False

    def _messages_to_prompt(self, messages: list[ChatMessage]) -> str:
        """Convert a messages list to a single prompt string for legacy completions."""
        parts = []
        for msg in messages:
            prefix = {"system": "System", "user": "User", "assistant": "Assistant"}.get(msg.role, msg.role.title())
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content) if msg.content else ""
            parts.append(f"{prefix}: {content}")
        parts.append("Assistant:")
        return "\n\n".join(parts)

    async def stream_completion(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream a completion via OpenAI-compatible API."""
        url, is_legacy = self._resolve_url()

        if is_legacy:
            body = self._build_legacy_body(messages)
        else:
            body = self._build_chat_body(messages, tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        input_tokens = 0
        output_tokens = 0
        pending_tool_calls: dict[int, dict] = {}

        try:
            async with self._client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    yield LLMEvent(type="error", text=f"API error {response.status_code}: {error_text}")
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # Extract usage if present
                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", input_tokens)
                        output_tokens = usage.get("completion_tokens", output_tokens)

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    if is_legacy:
                        # Legacy completions: choices[0].text
                        text = choices[0].get("text", "")
                        if text:
                            yield LLMEvent(type="text_delta", text=text)
                    else:
                        # Chat completions: choices[0].delta
                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason")

                        content = delta.get("content")
                        if content:
                            yield LLMEvent(type="text_delta", text=content)

                        # Tool calls (streamed incrementally)
                        tool_calls = delta.get("tool_calls", [])
                        for tc in tool_calls:
                            idx = tc.get("index", 0)
                            if idx not in pending_tool_calls:
                                pending_tool_calls[idx] = {
                                    "id": tc.get("id", ""),
                                    "name": tc.get("function", {}).get("name", ""),
                                    "arguments_json": "",
                                }
                            else:
                                if tc.get("id"):
                                    pending_tool_calls[idx]["id"] = tc["id"]
                                if tc.get("function", {}).get("name"):
                                    pending_tool_calls[idx]["name"] = tc["function"]["name"]

                            args_chunk = tc.get("function", {}).get("arguments", "")
                            if args_chunk:
                                pending_tool_calls[idx]["arguments_json"] += args_chunk

                        if finish_reason in ("tool_calls", "stop") and pending_tool_calls:
                            for idx in sorted(pending_tool_calls.keys()):
                                tc_data = pending_tool_calls[idx]
                                try:
                                    tool_input = json.loads(tc_data["arguments_json"])
                                except json.JSONDecodeError:
                                    tool_input = {"raw": tc_data["arguments_json"]}
                                yield LLMEvent(
                                    type="tool_call",
                                    tool_id=tc_data["id"],
                                    tool_name=tc_data["name"],
                                    tool_input=tool_input,
                                )
                            pending_tool_calls.clear()

        except httpx.ConnectError as e:
            yield LLMEvent(type="error", text=f"Connection failed: {e}")
            return
        except httpx.ReadTimeout:
            yield LLMEvent(type="error", text="Request timed out")
            return
        except Exception as e:
            yield LLMEvent(type="error", text=f"Unexpected error: {e}")
            return

        yield LLMEvent(
            type="done",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _build_chat_body(self, messages: list[ChatMessage], tools: list[dict] | None) -> dict:
        """Build request body for /chat/completions format."""
        msg_payload = []
        for msg in messages:
            entry: dict = {"role": msg.role}
            if msg.role == "tool":
                entry["content"] = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
                entry["tool_call_id"] = msg.tool_call_id
            else:
                entry["content"] = msg.content if msg.content is not None else ""
            if msg.name:
                entry["name"] = msg.name
            if msg.role == "assistant" and msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            msg_payload.append(entry)

        body: dict = {
            "model": self.model_name,
            "messages": msg_payload,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        return body

    def _build_legacy_body(self, messages: list[ChatMessage]) -> dict:
        """Build request body for /completions (legacy) format.

        Converts messages to a single prompt string. Tools are not supported
        in legacy completions mode.
        """
        return {
            "model": self.model_name,
            "prompt": self._messages_to_prompt(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }

    async def close(self) -> None:
        await self._client.aclose()
