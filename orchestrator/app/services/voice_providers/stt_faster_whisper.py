"""STT via the local stt-service container (faster-whisper)."""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.services.voice_providers.base import STTProvider

logger = logging.getLogger(__name__)


class FasterWhisperSTT(STTProvider):
    name = "faster_whisper"

    def __init__(self, service_url: str | None = None):
        self.service_url = (service_url or settings.stt_service_url).rstrip("/")

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        files = {"file": ("audio.m4a", audio, "audio/mp4")}
        data = {"language": language} if language else {}
        url = f"{self.service_url}/transcribe"
        timeout = httpx.Timeout(25.0, connect=5.0, read=25.0, write=10.0, pool=5.0)
        logger.warning(
            "faster-whisper STT request url=%s bytes=%d language=%s",
            url,
            len(audio),
            language,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url, files=files, data=data
            )
            logger.warning(
                "faster-whisper STT response status=%d bytes=%d",
                r.status_code,
                len(audio),
            )
            r.raise_for_status()
            text = (r.json().get("text") or "").strip()
            logger.warning("faster-whisper STT transcript chars=%d", len(text))
            return text
