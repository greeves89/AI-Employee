"""TTS via Azure Cognitive Services Speech — official Microsoft Neural Voices.

Same voice IDs as Edge-TTS (e.g. de-DE-KatjaNeural) but through the customer's
own Azure Speech resource (key + region), not the free Edge endpoint.

REST endpoint returns the full MP3 in one response; we stream it out in chunks
to match the TTSProvider AsyncIterator contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from xml.sax.saxutils import escape

import httpx

from app.services.voice_providers.base import TTSProvider

# Same curated neural voices as Edge — identical IDs work on Azure Speech.
AZURE_VOICES = [
    {"id": "de-DE-KatjaNeural", "label": "Katja (DE, weiblich)", "lang": "de-DE"},
    {"id": "de-DE-ConradNeural", "label": "Conrad (DE, männlich)", "lang": "de-DE"},
    {"id": "de-DE-AmalaNeural", "label": "Amala (DE, weiblich)", "lang": "de-DE"},
    {"id": "de-DE-KillianNeural", "label": "Killian (DE, männlich)", "lang": "de-DE"},
    {"id": "en-US-AvaNeural", "label": "Ava (EN-US, female)", "lang": "en-US"},
    {"id": "en-US-AndrewNeural", "label": "Andrew (EN-US, male)", "lang": "en-US"},
]

DEFAULT_VOICE = "de-DE-KatjaNeural"


class AzureSpeechTTS(TTSProvider):
    name = "azure_speech"
    output_mime = "audio/mpeg"

    def __init__(self, key: str, region: str, default_voice: str = DEFAULT_VOICE):
        if not key or not region:
            raise ValueError("Azure Speech key and region required for azure_speech TTS")
        self.key = key
        self.region = region
        self.default_voice = default_voice

    async def synthesize(
        self, text: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        v = voice or self.default_voice
        lang = "-".join(v.split("-")[:2]) if "-" in v else "de-DE"
        ssml = (
            f"<speak version='1.0' xml:lang='{lang}'>"
            f"<voice name='{v}'>{escape(text)}</voice></speak>"
        )
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "ai-employee-voice",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", url, headers=headers, content=ssml.encode("utf-8")) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk

    async def list_voices(self) -> list[dict]:
        return AZURE_VOICES
