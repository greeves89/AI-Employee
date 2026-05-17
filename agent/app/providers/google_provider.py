"""Google Gemini provider - uses the Gemini API with streaming."""

import json
import logging
from typing import AsyncIterator

import httpx

from app import multimodal
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent

logger = logging.getLogger(__name__)


def _gemini_image_parts(images: list[dict]) -> list[dict]:
    """Generic image blocks → Gemini inlineData parts."""
    return [
        {"inlineData": {"mimeType": im.get("media_type", "image/jpeg"), "data": im.get("data", "")}}
        for im in images
    ]


class GoogleProvider(BaseLLMProvider):
    """Provider for Google Gemini API (generateContent with streaming)."""

    async def _stream_completion_impl(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream a chat completion via Gemini API."""
        url = f"{self.api_endpoint}/models/{self.model_name}:streamGenerateContent?key={self.api_key}&alt=sse"

        # Convert messages to Gemini format
        system_instruction = None
        contents = []
        for msg in messages:
            text, images = multimodal.split_blocks(msg.content)
            if msg.role == "system":
                system_instruction = {"parts": [{"text": text}]}
            elif msg.role == "user":
                parts: list[dict] = [{"text": text}] if text else []
                parts.extend(_gemini_image_parts(images))
                contents.append({"role": "user", "parts": parts or [{"text": ""}]})
            elif msg.role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})
            elif msg.role == "tool":
                contents.append({
                    "role": "function",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.name,
                            "response": {"result": text or ("[image attached below]" if images else "")},
                        }
                    }],
                })
                if images:
                    contents.append({
                        "role": "user",
                        "parts": [
                            {"text": "Image(s) returned by the previous tool call:"},
                            *_gemini_image_parts(images),
                        ],
                    })

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        # Convert OpenAI tool format to Gemini format
        if tools:
            function_declarations = []
            for t in tools:
                func = t.get("function", {})
                function_declarations.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                })
            body["tools"] = [{"functionDeclarations": function_declarations}]

        headers = {"Content-Type": "application/json"}

        input_tokens = 0
        output_tokens = 0

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
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    candidates = chunk.get("candidates", [])
                    if not candidates:
                        continue

                    content = candidates[0].get("content", {})
                    for part in content.get("parts", []):
                        if "text" in part:
                            yield LLMEvent(type="text_delta", text=part["text"])
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            yield LLMEvent(
                                type="tool_call",
                                tool_id=f"call_{fc.get('name', 'unknown')}",
                                tool_name=fc.get("name", ""),
                                tool_input=fc.get("args", {}),
                            )

                    # Track usage metadata (Gemini reports this per chunk)
                    usage = chunk.get("usageMetadata", {})
                    if usage:
                        input_tokens = usage.get("promptTokenCount", input_tokens)
                        output_tokens = usage.get("candidatesTokenCount", output_tokens)

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
