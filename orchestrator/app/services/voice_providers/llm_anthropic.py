"""Voice LLM via Anthropic SDK — Haiku 4.5 for fast voice interaction.

Uses the same OAuth token / API key pattern as the rest of the orchestrator,
so admin configuration in PlatformSettings flows through automatically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.config import settings
from app.services.voice_providers.base import VoiceLLMProvider


class AnthropicVoiceLLM(VoiceLLMProvider):
    name = "anthropic"

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self.model = model

    def _client(self):
        from anthropic import AsyncAnthropic
        # API key path
        if settings.anthropic_api_key:
            return AsyncAnthropic(api_key=settings.anthropic_api_key)
        # OAuth token path (Claude Code)
        if settings.claude_code_oauth_token:
            return AsyncAnthropic(
                auth_token=settings.claude_code_oauth_token,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        raise RuntimeError(
            "No Anthropic credentials configured "
            "(anthropic_api_key or claude_code_oauth_token)"
        )

    async def stream_response(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        client = self._client()
        # Cache the system prompt: voice sessions reuse the same prompt across
        # many turns, so a single ephemeral breakpoint cuts ~90% of system-prompt
        # input tokens on every turn after the first within the 5-min TTL.
        async with client.messages.stream(
            model=self.model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
