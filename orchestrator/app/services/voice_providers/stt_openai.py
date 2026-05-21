"""STT via OpenAI Whisper API."""

from __future__ import annotations

import httpx

from app.services.voice_providers.base import STTProvider


class OpenAIWhisperSTT(STTProvider):
    name = "openai_whisper"

    def __init__(self, api_key: str, model: str = "whisper-1"):
        if not api_key:
            raise ValueError("OpenAI API key required for openai_whisper provider")
        self.api_key = api_key
        self.model = model

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        files = {"file": ("audio.webm", audio, "audio/webm")}
        data: dict[str, str] = {"model": self.model}
        if language:
            data["language"] = language
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers, files=files, data=data,
            )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
