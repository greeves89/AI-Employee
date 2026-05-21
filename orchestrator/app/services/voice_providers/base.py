"""Voice provider plugin interfaces.

STT, TTS, and Voice-LLM providers all implement these abstract bases.
The VoiceSessionManager picks the active provider at runtime based on
PlatformSettings, so admins can swap engines without code changes.

STT contract:
  - transcribe(audio: bytes, language: str | None) -> str
  - Accepts raw audio (webm/ogg/wav/pcm). Returns plain transcript.

TTS contract:
  - synthesize(text: str, voice: str | None) -> AsyncIterator[bytes]
  - Streams audio chunks (mp3 or opus) for low latency playback.

VoiceLLM contract:
  - stream_response(messages, system_prompt) -> AsyncIterator[str]
  - Streams text deltas for the interaction agent. Caller pipes deltas
    into TTS to overlap generation with synthesis.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator


class STTProvider(abc.ABC):
    """Speech-to-text provider."""

    name: str = "base"

    @abc.abstractmethod
    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        ...


class TTSProvider(abc.ABC):
    """Text-to-speech provider — streams audio chunks."""

    name: str = "base"
    # Default mime; subclasses override
    output_mime: str = "audio/mpeg"

    @abc.abstractmethod
    async def synthesize(
        self, text: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        ...

    async def list_voices(self) -> list[dict]:
        """Optional: return available voices [{"id":..., "label":..., "lang":...}]"""
        return []


class VoiceLLMProvider(abc.ABC):
    """LLM used by the interaction agent (fast model, e.g. Haiku)."""

    name: str = "base"

    @abc.abstractmethod
    async def stream_response(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        ...
