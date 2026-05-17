"""OpenAI-compatible provider (covers OpenAI, Azure, Together, Groq, vLLM, Ollama).

Supports three endpoint formats:
- Chat completions (default): /chat/completions  (messages-based)
- Responses API: /responses  (for Codex models like gpt-5.x-codex)
- Legacy completions: /completions  (prompt-based, deprecated)

Auto-detection priority:
1. If the endpoint URL explicitly ends with /chat/completions, /completions,
   or /responses → use that format.
2. Otherwise, detect from model name:
   - Codex models and the GPT-5.x family → Responses API (/responses)
   - Everything else → Chat Completions (/chat/completions)
"""

import json
import logging
from typing import AsyncIterator

import httpx

from app import multimodal
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent

logger = logging.getLogger(__name__)


def _data_uri(img: dict) -> str:
    return f"data:{img.get('media_type', 'image/jpeg')};base64,{img.get('data', '')}"


def _chat_image_parts(images: list[dict]) -> list[dict]:
    """Generic image blocks → OpenAI chat-completions image_url parts."""
    return [{"type": "image_url", "image_url": {"url": _data_uri(im)}} for im in images]

# Model-name substrings that require the Responses API instead of
# Chat Completions. The GPT-5.x family ("gpt-5", "gpt-5.4", "gpt-5.4-mini",
# Azure deployments named accordingly) is served via /responses.
_RESPONSES_API_PATTERNS = ("codex", "gpt-5", "gpt5")

def _extract_tokens_error(error_text: str) -> str | None:
    """Return the correct tokens param name if OpenAI tells us to use the other one."""
    if "max_completion_tokens" in error_text and "max_tokens" in error_text:
        if "Use 'max_completion_tokens'" in error_text:
            return "max_completion_tokens"
        if "Use 'max_tokens'" in error_text:
            return "max_tokens"
    return None


class OpenAIProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible APIs with streaming."""

    def __init__(self, **kwargs):
        self.thinking_mode = kwargs.pop("thinking_mode", "auto")  # "off", "auto", "on"
        self.is_azure = bool(kwargs.pop("is_azure", False))
        self.api_version = kwargs.pop("api_version", "") or ""
        super().__init__(**kwargs)

    def _is_responses_model(self) -> bool:
        """Check if the model requires the Responses API."""
        model_lower = self.model_name.lower()
        return any(p in model_lower for p in _RESPONSES_API_PATTERNS)

    def _azure_version(self, fmt: str = "chat") -> str:
        """api-version for Azure requests (account value, else a sane default).

        The Responses API needs a recent preview; chat/completions is fine on
        the stable GA version.
        """
        if self.api_version:
            return self.api_version
        return "2025-04-01-preview" if fmt == "responses" else "2024-10-21"

    def _headers(self) -> dict:
        """Auth headers — Azure OpenAI uses `api-key`, others `Bearer`."""
        h = {"Content-Type": "application/json"}
        if self.is_azure:
            h["api-key"] = self.api_key
        else:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _with_version(self, url: str, azure: bool, fmt: str = "chat") -> str:
        """Append api-version for Azure URLs that don't already carry one."""
        if azure and "api-version=" not in url:
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}api-version={self._azure_version(fmt)}"
        return url

    def _resolve_url(self) -> tuple[str, str]:
        """Determine the full URL and API format to use.

        Returns (url, format) where format is one of:
        "chat", "responses", "legacy".
        """
        ep = self.api_endpoint.rstrip("/")
        azure = self.is_azure or ".openai.azure.com" in ep

        # User specified the full path already → respect it verbatim.
        # (Azure's /openai/responses surface — e.g. for GPT-5 codex on
        # *.cognitiveservices.azure.com — is reached this way.)
        if ep.endswith("/chat/completions"):
            return self._with_version(ep, azure, "chat"), "chat"
        if ep.endswith("/responses"):
            return self._with_version(ep, azure, "responses"), "responses"
        if ep.endswith("/completions"):
            return self._with_version(ep, azure, "chat"), "legacy"

        if azure:
            # Classic Azure OpenAI: the deployment name lives in the path.
            # This surface serves deployments via /chat/completions — the
            # per-deployment /responses route is not exposed, so always
            # use chat completions (GPT-5 reasoning params handled below).
            root = ep.split("/openai")[0].rstrip("/")
            url = f"{root}/openai/deployments/{self.model_name}/chat/completions?api-version={self._azure_version()}"
            return url, "chat"

        fmt = "responses" if self._is_responses_model() else "chat"
        path = "responses" if fmt == "responses" else "chat/completions"
        return f"{ep}/{path}", fmt

    # ------------------------------------------------------------------ #
    # Main streaming entry point
    # ------------------------------------------------------------------ #

    async def _stream_completion_impl(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream a completion via OpenAI-compatible API."""
        url, fmt = self._resolve_url()
        logger.info("OpenAI provider: %s format=%s model=%s", url, fmt, self.model_name)

        if fmt == "responses":
            async for event in self._stream_responses(url, messages, tools):
                yield event
        elif fmt == "legacy":
            async for event in self._stream_legacy(url, messages):
                yield event
        else:
            async for event in self._stream_chat(url, messages, tools):
                yield event

    # ------------------------------------------------------------------ #
    # Responses API (/v1/responses)
    # ------------------------------------------------------------------ #

    async def _stream_responses(
        self, url: str, messages: list[ChatMessage], tools: list[dict] | None
    ) -> AsyncIterator[LLMEvent]:
        """Stream via the OpenAI Responses API."""
        body = self._build_responses_body(messages, tools)
        headers = self._headers()

        input_tokens = 0
        output_tokens = 0
        # Track function calls: item_id -> {name, arguments_json, call_id}
        pending_calls: dict[str, dict] = {}

        try:
            async with self.http.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    yield LLMEvent(type="error", text=f"API error {response.status_code}: {error_text}")
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")

                    # Text delta
                    if event_type == "response.output_text.delta":
                        delta = data.get("delta", "")
                        if delta:
                            yield LLMEvent(type="text_delta", text=delta)

                    # Function call: new item added
                    elif event_type == "response.output_item.added":
                        item = data.get("item", {})
                        if item.get("type") == "function_call":
                            item_id = item.get("id", "")
                            pending_calls[item_id] = {
                                "name": item.get("name", ""),
                                "call_id": item.get("call_id", item_id),
                                "arguments_json": "",
                            }

                    # Function call: arguments streaming
                    elif event_type == "response.function_call_arguments.delta":
                        item_id = data.get("item_id", "")
                        delta = data.get("delta", "")
                        if item_id in pending_calls and delta:
                            pending_calls[item_id]["arguments_json"] += delta

                    # Function call: arguments complete
                    elif event_type == "response.function_call_arguments.done":
                        item_id = data.get("item_id", "")
                        if item_id in pending_calls:
                            tc = pending_calls.pop(item_id)
                            try:
                                tool_input = json.loads(tc["arguments_json"])
                            except json.JSONDecodeError:
                                tool_input = {"raw": tc["arguments_json"]}
                            yield LLMEvent(
                                type="tool_call",
                                tool_id=tc["call_id"],
                                tool_name=tc["name"],
                                tool_input=tool_input,
                            )

                    # Response completed - extract usage
                    elif event_type == "response.completed":
                        resp = data.get("response", {})
                        usage = resp.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)

                        # Emit any remaining pending calls
                        for item_id, tc in pending_calls.items():
                            try:
                                tool_input = json.loads(tc["arguments_json"])
                            except json.JSONDecodeError:
                                tool_input = {"raw": tc["arguments_json"]}
                            yield LLMEvent(
                                type="tool_call",
                                tool_id=tc["call_id"],
                                tool_name=tc["name"],
                                tool_input=tool_input,
                            )
                        pending_calls.clear()

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

    def _build_responses_body(
        self, messages: list[ChatMessage], tools: list[dict] | None
    ) -> dict:
        """Build request body for /responses (Responses API) format."""
        # Extract system prompt as 'instructions'
        instructions = None
        input_items: list[dict] = []

        for msg in messages:
            if isinstance(msg.content, list):
                text, images = multimodal.split_blocks(msg.content)
            else:
                text = msg.content if isinstance(msg.content, str) else (json.dumps(msg.content) if msg.content else "")
                images = []
            content = text

            if msg.role == "system":
                instructions = content
            elif msg.role == "tool":
                # Tool results → function_call_output. Images can't ride
                # inside function_call_output, so attach them as a follow-up
                # user message with input_image parts.
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": content or ("[image returned — see next message]" if images else ""),
                })
                if images:
                    input_items.append({
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Image(s) returned by the previous tool call:"},
                            *[{"type": "input_image", "image_url": _data_uri(im)} for im in images],
                        ],
                    })
            elif msg.role == "assistant":
                if msg.tool_calls:
                    # Re-emit function calls so the API has context
                    for tc in msg.tool_calls:
                        func = tc.get("function", {})
                        input_items.append({
                            "type": "function_call",
                            "name": func.get("name", ""),
                            "call_id": tc.get("id", ""),
                            "arguments": func.get("arguments", "{}"),
                        })
                if content:
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    })
            else:
                # user messages — may carry attached images
                user_content: list[dict] = [{"type": "input_text", "text": content}]
                for im in images:
                    user_content.append({"type": "input_image", "image_url": _data_uri(im)})
                input_items.append({
                    "type": "message",
                    "role": "user",
                    "content": user_content,
                })

        body: dict = {
            "model": self.model_name,
            "input": input_items,
            "max_output_tokens": self.max_tokens,
            "stream": True,
        }

        if instructions:
            body["instructions"] = instructions
        # Codex models don't support temperature
        if self.temperature is not None and not self._is_responses_model():
            body["temperature"] = self.temperature

        # Tools in Responses API format
        if tools:
            resp_tools = []
            for t in tools:
                if t.get("type") == "function":
                    func = t.get("function", t)
                    resp_tools.append({
                        "type": "function",
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    })
                else:
                    resp_tools.append(t)
            body["tools"] = resp_tools

        return body

    # ------------------------------------------------------------------ #
    # Chat Completions API (/v1/chat/completions)
    # ------------------------------------------------------------------ #

    async def _stream_chat(
        self, url: str, messages: list[ChatMessage], tools: list[dict] | None
    ) -> AsyncIterator[LLMEvent]:
        """Stream via the Chat Completions API."""
        body = self._build_chat_body(messages, tools)
        headers = self._headers()
        async for event in self._stream_chat_with_body(url, headers, body):
            yield event

    async def _stream_chat_with_body(
        self, url: str, headers: dict, body: dict
    ) -> AsyncIterator[LLMEvent]:
        """Execute a single chat completions stream request. Retries once on tokens param mismatch."""
        input_tokens = 0
        output_tokens = 0
        pending_tool_calls: dict[int, dict] = {}

        try:
            async with self.http.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code == 400:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    correct_key = _extract_tokens_error(error_text)
                    if correct_key:
                        wrong_key = "max_tokens" if correct_key == "max_completion_tokens" else "max_completion_tokens"
                        body[correct_key] = body.pop(wrong_key, body.get(correct_key))
                        async with self.http.stream("POST", url, json=body, headers=headers) as retry:
                            if retry.status_code != 200:
                                err = (await retry.aread()).decode("utf-8", errors="replace")
                                yield LLMEvent(type="error", text=f"API error {retry.status_code}: {err}")
                                return
                            async for line in retry.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                data = line[6:]
                                if data == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data)
                                except json.JSONDecodeError:
                                    continue
                                for _ev in self._parse_chat_chunk(chunk, pending_tool_calls):
                                        yield _ev
                                usage = chunk.get("usage")
                                if usage:
                                    input_tokens = usage.get("prompt_tokens", input_tokens)
                                    output_tokens = usage.get("completion_tokens", output_tokens)
                        yield LLMEvent(type="done", input_tokens=input_tokens, output_tokens=output_tokens)
                        return
                    yield LLMEvent(type="error", text=f"API error {response.status_code}: {error_text}")
                    return
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

                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", input_tokens)
                        output_tokens = usage.get("completion_tokens", output_tokens)

                    for _ev in self._parse_chat_chunk(chunk, pending_tool_calls):
                        yield _ev

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

    def _parse_chat_chunk(
        self, chunk: dict, pending_tool_calls: dict[int, dict]
    ) -> list[LLMEvent]:
        """Parse a single SSE chunk from Chat Completions stream into LLMEvents."""
        events = []
        choices = chunk.get("choices", [])
        if not choices:
            return events

        delta = choices[0].get("delta", {})
        finish_reason = choices[0].get("finish_reason")

        content = delta.get("content")
        if content:
            events.append(LLMEvent(type="text_delta", text=content))

        for tc in delta.get("tool_calls", []):
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
                events.append(LLMEvent(
                    type="tool_call",
                    tool_id=tc_data["id"],
                    tool_name=tc_data["name"],
                    tool_input=tool_input,
                ))
            pending_tool_calls.clear()

        return events

    def _build_chat_body(self, messages: list[ChatMessage], tools: list[dict] | None) -> dict:
        """Build request body for /chat/completions format."""
        msg_payload = []
        # OpenAI tool messages cannot carry images and must follow the
        # assistant tool_calls message contiguously. Tool-result images are
        # therefore buffered and flushed as a user message once the run of
        # tool messages ends.
        deferred_images: list[dict] = []

        def _flush_images():
            if deferred_images:
                msg_payload.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Image(s) returned by the preceding tool call(s):"},
                        *_chat_image_parts(deferred_images),
                    ],
                })
                deferred_images.clear()

        for msg in messages:
            if msg.role == "tool":
                text, images = multimodal.split_blocks(msg.content)
                entry: dict = {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": text or ("[image returned — see next message]" if images else ""),
                }
                if msg.name:
                    entry["name"] = msg.name
                msg_payload.append(entry)
                deferred_images.extend(images)
                continue

            _flush_images()
            entry = {"role": msg.role}
            if isinstance(msg.content, list):
                text, images = multimodal.split_blocks(msg.content)
                if images:
                    parts: list[dict] = []
                    if text:
                        parts.append({"type": "text", "text": text})
                    parts.extend(_chat_image_parts(images))
                    entry["content"] = parts
                else:
                    entry["content"] = text
            else:
                entry["content"] = msg.content if msg.content is not None else ""
            if msg.name:
                entry["name"] = msg.name
            if msg.role == "assistant" and msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            msg_payload.append(entry)

        _flush_images()

        body: dict = {
            "model": self.model_name,
            "messages": msg_payload,
            "stream": True,
        }
        # GPT-5 / o-series / codex reasoning models require
        # `max_completion_tokens` and reject a custom `temperature`.
        if self._is_responses_model():
            body["max_completion_tokens"] = self.max_tokens
        else:
            body["max_tokens"] = self.max_tokens
            body["temperature"] = self.temperature

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # Thinking/reasoning mode (Qwen 3.5, DeepSeek R1, etc.)
        # Modes: "off" = never think, "auto" = model decides, "on" = always think
        thinking_mode = getattr(self, "thinking_mode", "auto")
        if thinking_mode == "off":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        elif thinking_mode == "on":
            body["chat_template_kwargs"] = {"enable_thinking": True}
        # "auto" = don't send the flag, let the model decide (hybrid)

        return body

    # ------------------------------------------------------------------ #
    # Legacy Completions API (/v1/completions) - deprecated
    # ------------------------------------------------------------------ #

    async def _stream_legacy(
        self, url: str, messages: list[ChatMessage]
    ) -> AsyncIterator[LLMEvent]:
        """Stream via the legacy Completions API."""
        body = self._build_legacy_body(messages)
        headers = self._headers()

        input_tokens = 0
        output_tokens = 0

        try:
            async with self.http.stream("POST", url, json=body, headers=headers) as response:
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

                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", input_tokens)
                        output_tokens = usage.get("completion_tokens", output_tokens)

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    text = choices[0].get("text", "")
                    if text:
                        yield LLMEvent(type="text_delta", text=text)

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

    def _build_legacy_body(self, messages: list[ChatMessage]) -> dict:
        """Build request body for /completions (legacy) format."""
        return {
            "model": self.model_name,
            "prompt": self._messages_to_prompt(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }

    def _messages_to_prompt(self, messages: list[ChatMessage]) -> str:
        """Convert a messages list to a single prompt string for legacy completions."""
        parts = []
        for msg in messages:
            prefix = {"system": "System", "user": "User", "assistant": "Assistant"}.get(msg.role, msg.role.title())
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content) if msg.content else ""
            parts.append(f"{prefix}: {content}")
        parts.append("Assistant:")
        return "\n\n".join(parts)
