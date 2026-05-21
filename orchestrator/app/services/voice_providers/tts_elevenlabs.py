"""TTS via ElevenLabs — premium quality, voice cloning, paid."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.services.voice_providers.base import TTSProvider


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    output_mime = "audio/mpeg"

    def __init__(self, api_key: str, default_voice: str = "21m00Tcm4TlvDq8ikWAM"):
        if not api_key:
            raise ValueError("ElevenLabs API key required")
        self.api_key = api_key
        self.default_voice = default_voice

    async def synthesize(
        self, text: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        voice_id = voice or self.default_voice
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk

    async def list_voices(self) -> list[dict]:
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {"xi-api-key": self.api_key}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return [
                {"id": v["voice_id"], "label": v["name"], "lang": "multi"}
                for v in r.json().get("voices", [])
            ]
