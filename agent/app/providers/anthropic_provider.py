"""Anthropic provider - uses the Messages API with streaming."""

import json
import logging
from typing import AsyncIterator

import httpx

from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent, format_exception

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


class AnthropicProvider(BaseLLMProvider):
    """Provider for the Anthropic Messages API (claude models via direct API)."""

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
            "max_tokens": self.max_tokens,
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
            anthropic_tools = []
            for t in tools:
                func = t.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            if anthropic_tools:
                # One cache breakpoint at the end of the tool list caches the
                # whole static prefix (system + all tool definitions).
                anthropic_tools[-1] = {
                    **anthropic_tools[-1],
                    "cache_control": {"type": "ephemeral"},
                }
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
            yield LLMEvent(type="error", text=f"Unexpected error: {format_exception(e)}")
            return

        yield LLMEvent(type="done", input_tokens=input_tokens, output_tokens=output_tokens)
