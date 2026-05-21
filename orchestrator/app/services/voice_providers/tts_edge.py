"""TTS via Microsoft Edge-TTS — free, no API key, MS Neural Voices.

Streams MP3 chunks for low-latency playback. Uses the edge-tts Python lib
which talks to MS Speech endpoint directly (same as Edge browser).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.services.voice_providers.base import TTSProvider


# Curated subset; full list is huge. Admin picks from this in settings.
EDGE_VOICES = [
    {"id": "de-DE-KatjaNeural", "label": "Katja (DE, weiblich)", "lang": "de-DE"},
    {"id": "de-DE-ConradNeural", "label": "Conrad (DE, männlich)", "lang": "de-DE"},
    {"id": "de-DE-AmalaNeural", "label": "Amala (DE, weiblich)", "lang": "de-DE"},
    {"id": "de-DE-KillianNeural", "label": "Killian (DE, männlich)", "lang": "de-DE"},
    {"id": "en-US-AvaNeural", "label": "Ava (EN-US, female)", "lang": "en-US"},
    {"id": "en-US-AndrewNeural", "label": "Andrew (EN-US, male)", "lang": "en-US"},
    {"id": "en-GB-LibbyNeural", "label": "Libby (EN-GB, female)", "lang": "en-GB"},
]

DEFAULT_VOICE = "de-DE-KatjaNeural"


class EdgeTTS(TTSProvider):
    name = "edge_tts"
    output_mime = "audio/mpeg"

    def __init__(self, default_voice: str = DEFAULT_VOICE):
        self.default_voice = default_voice

    async def synthesize(
        self, text: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        import edge_tts  # lazy import — keeps cold start lean
        v = voice or self.default_voice
        communicate = edge_tts.Communicate(text, v)
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                yield chunk["data"]

    async def list_voices(self) -> list[dict]:
        return EDGE_VOICES
