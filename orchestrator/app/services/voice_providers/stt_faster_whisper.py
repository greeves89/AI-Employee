"""STT via the local stt-service container (faster-whisper)."""

from __future__ import annotations

import httpx

from app.config import settings
from app.services.voice_providers.base import STTProvider


class FasterWhisperSTT(STTProvider):
    name = "faster_whisper"

    def __init__(self, service_url: str | None = None):
        self.service_url = (service_url or settings.stt_service_url).rstrip("/")

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        files = {"file": ("audio.webm", audio, "audio/webm")}
        data = {"language": language} if language else {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{self.service_url}/transcribe", files=files, data=data
            )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
