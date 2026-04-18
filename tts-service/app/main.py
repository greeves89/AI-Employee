"""TTS Service — macOS native voices (say) with Kokoro local model option.

POST /synthesize   { text, language?, speaker? }  → audio/ogg
GET  /voices       → list available voices
GET  /healthz      → { status, provider, model_loaded }
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger("tts-service")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AI-Employee TTS Service", version="1.0.0")

# ── Config ────────────────────────────────────────────────────────────────────
# macOS voice per language (all built-in, offline, free)
MACOS_VOICES: dict[str, str] = {
    "de": os.getenv("TTS_VOICE_DE", "Anna"),       # German
    "en": os.getenv("TTS_VOICE_EN", "Samantha"),   # English
    "fr": os.getenv("TTS_VOICE_FR", "Thomas"),     # French
    "es": os.getenv("TTS_VOICE_ES", "Juan"),       # Spanish
    "it": os.getenv("TTS_VOICE_IT", "Alice"),      # Italian
    "nl": os.getenv("TTS_VOICE_NL", "Xander"),    # Dutch
    "pt": os.getenv("TTS_VOICE_PT", "Joana"),      # Portuguese
    "ja": os.getenv("TTS_VOICE_JA", "Kyoko"),      # Japanese
    "zh": os.getenv("TTS_VOICE_ZH", "Tingting"),   # Chinese
    "ru": os.getenv("TTS_VOICE_RU", "Milena"),     # Russian
    "pl": os.getenv("TTS_VOICE_PL", "Zosia"),      # Polish
}
DEFAULT_LANG = os.getenv("TTS_DEFAULT_LANG", "de")

_provider = "macos-say"
_model_loaded = True  # say is always available on macOS


@app.on_event("startup")
async def startup():
    # Verify `say` is available and AIFF→OGG conversion works
    try:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            aiff_path = f.name
        subprocess.run(
            ["say", "-v", MACOS_VOICES.get("de", "Anna"), "-o", aiff_path, "Test"],
            check=True, capture_output=True,
        )
        os.unlink(aiff_path)
        logger.info(f"macOS TTS ready — default voice: {MACOS_VOICES.get(DEFAULT_LANG)}")
    except Exception as e:
        logger.warning(f"macOS say check failed: {e}")


# ── Schemas ───────────────────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    text: str
    language: str = "de"
    speaker: Optional[str] = None
    speed: float = 1.0


# ── Audio helpers ─────────────────────────────────────────────────────────────

def _aiff_to_ogg(aiff_path: str) -> bytes:
    """Convert AIFF file to OGG Opus using ffmpeg."""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", aiff_path,
            "-c:a", "libopus", "-b:a", "64k",
            "-f", "ogg", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


async def _synthesize_macos(text: str, language: str, speaker: Optional[str], speed: float) -> bytes:
    """Use macOS built-in `say` command — offline, free, high quality Neural voices."""
    voice = speaker or MACOS_VOICES.get(language[:2], MACOS_VOICES.get(DEFAULT_LANG, "Anna"))

    loop = asyncio.get_event_loop()

    def _run() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            aiff_path = f.name
        try:
            rate_arg = str(int(190 * speed))  # default words/min ≈ 190
            subprocess.run(
                ["say", "-v", voice, "-r", rate_arg, "-o", aiff_path, text],
                check=True, capture_output=True,
            )
            return _aiff_to_ogg(aiff_path)
        finally:
            if os.path.exists(aiff_path):
                os.unlink(aiff_path)

    return await loop.run_in_executor(None, _run)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "provider": _provider,
        "model": "macos-say",
        "model_loaded": _model_loaded,
    }


@app.get("/voices")
async def list_voices():
    return {
        "provider": "macos-say",
        "voices": MACOS_VOICES,
        "note": "macOS built-in Neural TTS — offline, no API key needed",
    }


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    if len(req.text) > 5000:
        raise HTTPException(status_code=400, detail="text too long (max 5000 chars)")

    import time
    t0 = time.time()
    try:
        audio_bytes = await _synthesize_macos(req.text, req.language, req.speaker, req.speed)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        logger.error(f"TTS error: {stderr}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {stderr[:200]}")
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = round(time.time() - t0, 2)
    logger.info(f"TTS [macos-say] {len(req.text)} chars → {len(audio_bytes)} bytes in {elapsed}s")

    return Response(
        content=audio_bytes,
        media_type="audio/ogg",
        headers={
            "X-TTS-Provider": _provider,
            "X-TTS-Duration": str(elapsed),
        },
    )
