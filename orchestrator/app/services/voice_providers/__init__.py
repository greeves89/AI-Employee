"""Voice provider plugins — STT, TTS, and Voice-LLM engines.

Active provider is chosen by admin via PlatformSettings; see registry.get_*().
"""

from app.services.voice_providers.registry import (
    get_active_voice_config,
    get_llm,
    get_stt,
    get_tts,
)

__all__ = ["get_stt", "get_tts", "get_llm", "get_active_voice_config"]
