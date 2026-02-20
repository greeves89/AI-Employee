"""Google Gemini provider - uses the Gemini API with streaming."""

import json
import logging
from typing import AsyncIterator

import httpx

from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent

logger = logging.getLogger(__name__)


class GoogleProvider(BaseLLMProvider):
    """Provider for Google Gemini API (generateContent with streaming)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def stream_completion(
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
            if msg.role == "system":
                system_instruction = {"parts": [{"text": msg.content if isinstance(msg.content, str) else str(msg.content)}]}
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [{"text": msg.content if isinstance(msg.content, str) else str(msg.content)}]})
            elif msg.role == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg.content if isinstance(msg.content, str) else str(msg.content)}]})
            elif msg.role == "tool":
                contents.append({
                    "role": "function",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.name,
                            "response": {"result": msg.content if isinstance(msg.content, str) else str(msg.content)},
                        }
                    }],
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

                    # Check usage metadata
                    usage = chunk.get("usageMetadata", {})
                    if usage:
                        input_tokens = usage.get("promptTokenCount", 0)
                        output_tokens = usage.get("candidatesTokenCount", 0)

        except httpx.ConnectError as e:
            yield LLMEvent(type="error", text=f"Connection failed: {e}")
            return
        except httpx.ReadTimeout:
            yield LLMEvent(type="error", text="Request timed out")
            return
        except Exception as e:
            yield LLMEvent(type="error", text=f"Unexpected error: {e}")
            return

        yield LLMEvent(type="done")

    async def close(self) -> None:
        await self._client.aclose()
