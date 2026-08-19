"""Anthropic provider - uses the Messages API with streaming."""

import json
import logging
import time
from typing import AsyncIterator

import httpx

from app.providers.base import (
    BaseLLMProvider,
    ChatMessage,
    LLMEvent,
    describe_failure,
)

logger = logging.getLogger(__name__)


def _to_anthropic_blocks(content) -> list[dict]:
    """Convert generic content blocks (text/image) to Anthropic block format."""
    blocks: list[dict] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            blocks.append({"type": "text", "text": str(block)})
        elif block.get("type") == "image":
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.get("media_type", "image/jpeg"),
                    "data": block.get("data", ""),
                },
            })
        elif block.get("type") == "text":
            blocks.append({"type": "text", "text": block.get("text", "")})
        else:
            blocks.append(block)
    return blocks


def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style function tools to Anthropic tool blocks.

    Anthropic strictly rejects the whole request (400) if a tool name is empty
    or repeated; OpenAI tolerates duplicates. The upstream catalog can carry
    name collisions (built-in vs orchestrator API vs MCP), so enforce
    uniqueness here (first occurrence wins) — this is the single choke point for
    every Anthropic call (chat, tasks, messages). A single cache breakpoint on
    the last tool caches the whole static prefix (system + all tool defs).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for t in tools or []:
        func = t.get("function", {})
        name = func.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {}),
        })
    if out:
        out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


class AnthropicProvider(BaseLLMProvider):
    """Provider for the Anthropic Messages API (claude models via direct API)."""

    #: Antwortlänge je Modellfamilie, wenn keine eigene Grenze gesetzt ist.
    #: Anders als bei OpenAI und Google ist ``max_tokens`` bei Anthropic ein
    #: **Pflichtfeld** — weglassen geht nicht, es muss eine Zahl hinein.
    #:
    #: Zu hoch ist keine sichere Wahl: die API weist einen Wert oberhalb des
    #: Modellmaximums mit 400 ab, und dann antwortet der Agent gar nicht mehr.
    #: Deshalb je Familie der dort erlaubte Wert, und im Zweifel der niedrigste.
    _FAMILY_MAX_TOKENS: tuple[tuple[str, int], ...] = (
        ("claude-opus-4", 32_000),
        ("claude-sonnet-4", 64_000),
        ("claude-haiku-4", 64_000),
        ("claude-opus-5", 64_000),
        ("claude-sonnet-5", 64_000),
        ("claude-fable-5", 64_000),
        ("claude-3-7", 64_000),
        ("claude-3-5-haiku", 8_192),
        ("claude-3-5", 8_192),
        ("claude-3", 4_096),
    )
    _SAFE_DEFAULT_MAX_TOKENS = 8_192

    def _max_tokens_for_request(self) -> int:
        """Was in ``max_tokens`` geschrieben wird.

        Eine gesetzte Grenze gilt unverändert. Ohne Grenze (0) wird nicht gekappt,
        sondern der für diese Modellfamilie erlaubte Höchstwert genommen — ein
        unbekanntes Modell bekommt den überall gültigen Wert, damit ein Tippfehler
        im Modellnamen nicht in ein 400 läuft.
        """
        if self.max_tokens:
            return self.max_tokens
        name = (self.model_name or "").lower()
        for prefix, value in self._FAMILY_MAX_TOKENS:
            if prefix in name:
                return value
        return self._SAFE_DEFAULT_MAX_TOKENS

    async def _stream_completion_impl(
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
                # tool_result content may be plain text OR a list of blocks
                # (image-aware tools). Anthropic accepts image blocks inside
                # tool_result directly — Claude sees the image natively.
                if isinstance(msg.content, list):
                    tr_content: object = _to_anthropic_blocks(msg.content)
                else:
                    tr_content = msg.content if isinstance(msg.content, str) else str(msg.content)
                conv_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id or "",
                        "content": tr_content,
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                # An assistant turn that called tools must carry tool_use
                # content blocks, else the following tool_result blocks are
                # rejected ("no corresponding tool_use block").
                blocks: list[dict] = []
                if msg.content:
                    txt = msg.content if isinstance(msg.content, str) else str(msg.content)
                    if txt.strip():
                        blocks.append({"type": "text", "text": txt})
                for tc in msg.tool_calls:
                    fn = tc.get("function", {}) or {}
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        tool_input = args
                    elif isinstance(args, str) and args:
                        try:
                            tool_input = json.loads(args)
                        except json.JSONDecodeError:
                            tool_input = {}
                    else:
                        tool_input = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": tool_input,
                    })
                conv_messages.append({"role": "assistant", "content": blocks})
            else:
                # User/assistant messages — a list means generic content
                # blocks (e.g. a Telegram photo attached to a user message).
                if isinstance(msg.content, list):
                    content: object = _to_anthropic_blocks(msg.content)
                else:
                    content = msg.content
                conv_messages.append({"role": msg.role, "content": content})

        body: dict = {
            "model": self.model_name,
            "messages": conv_messages,
            "max_tokens": self._max_tokens_for_request(),
            "stream": True,
        }
        # Prompt caching: the system prompt + tool definitions are large and
        # static across every turn of a task. Marking them with cache_control
        # lets Anthropic serve them from cache — big cost/latency win on
        # multi-turn runs. The (changing) conversation after them is not cached.
        if system_text:
            body["system"] = [{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }]

        # Convert OpenAI tool format to Anthropic format
        if tools:
            anthropic_tools = _to_anthropic_tools(tools)
            if anthropic_tools:
                body["tools"] = anthropic_tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        cache_write_tokens = 0
        current_tool_id = ""
        current_tool_name = ""
        current_tool_json = ""

        _start = time.monotonic()

        def _diag(e):
            return describe_failure(e, url=url, body=body, messages=messages,
                                    model=self.model, started=_start)

        try:
            async with self.http.stream("POST", url, json=body, headers=headers) as response:
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
                        # Claude meldet den Prompt-Cache getrennt: neu geschrieben
                        # vs. gelesen. (Kein separates reasoning_tokens — das
                        # „Denken" zählt bei Claude zu output_tokens.)
                        cache_write_tokens = int(usage.get("cache_creation_input_tokens") or 0)
                        cached_tokens = int(usage.get("cache_read_input_tokens") or 0)

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
            yield LLMEvent(type="error", text=f"Connection failed: {_diag(e)}")
            return
        except httpx.ReadTimeout as e:
            yield LLMEvent(type="error", text=f"Request timed out: {_diag(e)}")
            return
        except Exception as e:
            yield LLMEvent(type="error", text=f"Unexpected error: {_diag(e)}")
            return

        yield LLMEvent(type="done", input_tokens=input_tokens, output_tokens=output_tokens,
                       cached_tokens=cached_tokens, cache_write_tokens=cache_write_tokens)
