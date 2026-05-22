"""Voice provider registry — instantiates the active provider trio.

Reads PlatformSettings via SettingsService and returns concrete provider
objects. Each call constructs fresh instances so settings changes propagate
without restarting the orchestrator.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.settings_service import SettingsService
from app.services.voice_providers.base import (
    STTProvider, TTSProvider, VoiceLLMProvider,
)
from app.services.voice_providers.llm_anthropic import AnthropicVoiceLLM
from app.services.voice_providers.stt_faster_whisper import FasterWhisperSTT
from app.services.voice_providers.stt_openai import OpenAIWhisperSTT
from app.services.voice_providers.tts_edge import EdgeTTS, DEFAULT_VOICE as EDGE_DEFAULT
from app.services.voice_providers.tts_elevenlabs import ElevenLabsTTS

logger = logging.getLogger(__name__)


STT_PROVIDERS = ["faster_whisper", "openai_whisper"]
TTS_PROVIDERS = ["edge_tts", "elevenlabs"]
LLM_PROVIDERS = ["anthropic"]
DEFAULT_LANGUAGE = "de"


async def get_stt(db: AsyncSession) -> STTProvider:
    svc = SettingsService(db)
    provider = (await svc.get("voice_stt_provider")) or "faster_whisper"
    if provider == "openai_whisper":
        key = (await svc.get("voice_openai_api_key")) or settings.openai_api_key
        return OpenAIWhisperSTT(api_key=key)
    return FasterWhisperSTT()


async def get_tts(db: AsyncSession) -> TTSProvider:
    svc = SettingsService(db)
    provider = (await svc.get("voice_tts_provider")) or "edge_tts"
    voice = (await svc.get("voice_tts_voice")) or EDGE_DEFAULT
    if provider == "elevenlabs":
        key = await svc.get("voice_elevenlabs_api_key") or ""
        return ElevenLabsTTS(api_key=key, default_voice=voice)
    return EdgeTTS(default_voice=voice)


async def get_llm(db: AsyncSession) -> VoiceLLMProvider:
    svc = SettingsService(db)
    model = (await svc.get("voice_llm_model")) or "claude-haiku-4-5-20251001"
    return AnthropicVoiceLLM(model=model)


async def get_active_voice_config(db: AsyncSession) -> dict:
    """Return the current voice provider config for the admin UI."""
    svc = SettingsService(db)
    return {
        "stt_provider": (await svc.get("voice_stt_provider")) or "faster_whisper",
        "tts_provider": (await svc.get("voice_tts_provider")) or "edge_tts",
        "tts_voice": (await svc.get("voice_tts_voice")) or EDGE_DEFAULT,
        "llm_model": (await svc.get("voice_llm_model")) or "claude-haiku-4-5-20251001",
        "language": (await svc.get("voice_language")) or DEFAULT_LANGUAGE,
        "available_stt": STT_PROVIDERS,
        "available_tts": TTS_PROVIDERS,
        "available_llm": LLM_PROVIDERS,
        "has_openai_key": bool(
            (await svc.get("voice_openai_api_key")) or settings.openai_api_key
        ),
        "has_elevenlabs_key": bool(await svc.get("voice_elevenlabs_api_key")),
    }
