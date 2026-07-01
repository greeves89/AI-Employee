"""STT via Azure Cognitive Services Speech (the official Microsoft voice service).

Uses the short-audio REST endpoint (no heavy SDK / system libs in the image).
Needs an Azure Speech resource key + region.

NOTE (needs live validation): the browser captures webm/opus (MediaRecorder).
Azure's short-audio REST API officially accepts wav and ogg/opus; webm/opus is a
sibling container. If Azure rejects webm in the customer's region, the audio must
be transcoded (or the realtime mode / faster-whisper used instead). Flagged for
the live test with the customer's key.
"""

from __future__ import annotations

import httpx

from app.services.voice_providers.base import STTProvider


class AzureSpeechSTT(STTProvider):
    name = "azure_speech"

    def __init__(self, key: str, region: str, language: str = "de-DE"):
        if not key or not region:
            raise ValueError("Azure Speech key and region required for azure_speech STT")
        self.key = key
        self.region = region
        self.language = language

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        lang = language or self.language or "de-DE"
        # Azure expects a full locale (de-DE); normalize a bare "de".
        if len(lang) == 2:
            lang = {"de": "de-DE", "en": "en-US"}.get(lang, lang)
        url = (
            f"https://{self.region}.stt.speech.microsoft.com"
            f"/speech/recognition/conversation/cognitiveservices/v1?language={lang}"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            # Container reported by MediaRecorder. May need transcode if rejected.
            "Content-Type": "audio/webm; codecs=opus",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, headers=headers, content=audio)
            r.raise_for_status()
            data = r.json()
        # RecognitionStatus: "Success" → DisplayText
        if data.get("RecognitionStatus") == "Success":
            return (data.get("DisplayText") or "").strip()
        return ""
