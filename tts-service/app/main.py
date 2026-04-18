"""TTS Service — edge-tts (Conrad/Neural voices) with macOS say fallback.

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
# edge-tts voices per language (Microsoft Neural — free, no API key)
EDGE_TTS_VOICES: dict[str, str] = {
    "de": os.getenv("TTS_VOICE_DE", "de-DE-ConradNeural"),
    "en": os.getenv("TTS_VOICE_EN", "en-US-GuyNeural"),
    "fr": os.getenv("TTS_VOICE_FR", "fr-FR-HenriNeural"),
    "es": os.getenv("TTS_VOICE_ES", "es-ES-AlvaroNeural"),
    "it": os.getenv("TTS_VOICE_IT", "it-IT-DiegoNeural"),
    "nl": os.getenv("TTS_VOICE_NL", "nl-NL-MaartenNeural"),
    "pt": os.getenv("TTS_VOICE_PT", "pt-BR-AntonioNeural"),
    "ru": os.getenv("TTS_VOICE_RU", "ru-RU-DmitryNeural"),
    "ja": os.getenv("TTS_VOICE_JA", "ja-JP-KeitaNeural"),
    "zh": os.getenv("TTS_VOICE_ZH", "zh-CN-YunxiNeural"),
    "pl": os.getenv("TTS_VOICE_PL", "pl-PL-MarekNeural"),
}

# macOS fallback voices per language
MACOS_VOICES: dict[str, str] = {
    "de": "Anna", "en": "Samantha", "fr": "Thomas",
    "es": "Juan", "it": "Alice", "ja": "Kyoko",
    "zh": "Tingting", "ru": "Milena", "pt": "Joana",
}

DEFAULT_LANG = os.getenv("TTS_DEFAULT_LANG", "de")

_provider = "edge-tts"
_model_loaded = True


@app.on_event("startup")
async def startup():
    logger.info(f"TTS Service ready — primary: edge-tts ({EDGE_TTS_VOICES.get('de')}), fallback: macOS say")


# ── Schemas ───────────────────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    text: str
    language: str = "de"
    speaker: Optional[str] = None
    speed: float = 1.0


# ── Synthesis ─────────────────────────────────────────────────────────────────

async def _synthesize_edge_tts(text: str, language: str, speaker: Optional[str]) -> bytes:
    """edge-tts — Microsoft Neural voices, free, no API key."""
    import edge_tts

    voice = speaker or EDGE_TTS_VOICES.get(language[:2], EDGE_TTS_VOICES["de"])
    communicate = edge_tts.Communicate(text, voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    data = buf.read()
    if not data:
        raise RuntimeError("edge-tts returned empty audio")
    return data


def _aiff_to_ogg(aiff_path: str) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", aiff_path, "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1"],
        capture_output=True, check=True,
    )
    return result.stdout


async def _synthesize_macos(text: str, language: str, speaker: Optional[str]) -> bytes:
    """macOS built-in say — offline fallback."""
    voice = speaker or MACOS_VOICES.get(language[:2], "Anna")
    loop = asyncio.get_event_loop()

    def _run() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            aiff_path = f.name
        try:
            subprocess.run(["say", "-v", voice, "-o", aiff_path, text], check=True, capture_output=True)
            return _aiff_to_ogg(aiff_path)
        finally:
            if os.path.exists(aiff_path):
                os.unlink(aiff_path)

    return await loop.run_in_executor(None, _run)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "provider": _provider, "model": "edge-tts", "model_loaded": _model_loaded}


@app.get("/voices")
async def list_voices():
    return {"provider": "edge-tts", "voices": EDGE_TTS_VOICES, "fallback": MACOS_VOICES}


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    if len(req.text) > 5000:
        raise HTTPException(status_code=400, detail="text too long (max 5000 chars)")

    import time
    t0 = time.time()
    used = "edge-tts"

    try:
        audio_bytes = await _synthesize_edge_tts(req.text, req.language, req.speaker)
    except Exception as e:
        logger.warning(f"edge-tts failed ({e}), falling back to macOS say")
        try:
            audio_bytes = await _synthesize_macos(req.text, req.language, req.speaker)
            used = "macos-say"
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"TTS failed: {e2}")

    elapsed = round(time.time() - t0, 2)
    logger.info(f"TTS [{used}] {len(req.text)} chars → {len(audio_bytes)} bytes in {elapsed}s")

    return Response(
        content=audio_bytes,
        media_type="audio/ogg",
        headers={"X-TTS-Provider": used, "X-TTS-Duration": str(elapsed)},
    )
