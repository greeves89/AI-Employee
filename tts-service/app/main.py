"""TTS Service — VibeVoice (microsoft/VibeVoice-1.5B) with edge-tts fallback.

POST /synthesize   { text, language?, speaker? }  → audio/ogg
GET  /voices       → list available speakers
GET  /healthz      → { status, provider, model_loaded }
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import time
from typing import Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger("tts-service")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AI-Employee TTS Service", version="1.0.0")

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_NAME = os.getenv("TTS_MODEL", "microsoft/VibeVoice-1.5B")
MODEL_CACHE = os.getenv("HF_HOME", "/models")
FALLBACK_VOICE = os.getenv("EDGE_TTS_VOICE", "de-DE-ConradNeural")

# ── Model state ───────────────────────────────────────────────────────────────
_tts_pipeline = None
_model_loaded = False
_provider = "loading"


def _load_model() -> bool:
    global _tts_pipeline, _model_loaded, _provider
    try:
        logger.info(f"Loading TTS model: {MODEL_NAME}")
        import torch
        from transformers import pipeline as hf_pipeline

        # Prefer Apple Metal (MPS) → CUDA → CPU
        if torch.backends.mps.is_available():
            device = "mps"
            logger.info("Using Apple Metal (MPS) GPU acceleration")
        elif torch.cuda.is_available():
            device = "cuda"
            logger.info("Using CUDA GPU acceleration")
        else:
            device = "cpu"
            logger.info("Using CPU (no GPU available)")

        _tts_pipeline = hf_pipeline(
            "text-to-speech",
            model=MODEL_NAME,
            device=device,
        )
        _model_loaded = True
        _provider = "vibevoice"
        logger.info(f"Model loaded: {MODEL_NAME}")
        return True
    except Exception as e:
        logger.warning(f"VibeVoice load failed ({e}), using edge-tts fallback")
        _model_loaded = False
        _provider = "edge-tts"
        return False


@app.on_event("startup")
async def startup():
    # Load model in background so healthz responds immediately
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _load_model)


# ── Schemas ───────────────────────────────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    text: str
    language: str = "de"
    speaker: Optional[str] = None
    speed: float = 1.0


# ── Audio helpers ─────────────────────────────────────────────────────────────

def _numpy_to_ogg(audio_array: np.ndarray, sample_rate: int) -> bytes:
    """Convert numpy float32 audio array to OGG Opus bytes via soundfile."""
    buf = io.BytesIO()
    # Normalize
    if audio_array.dtype != np.float32:
        audio_array = audio_array.astype(np.float32)
    peak = np.abs(audio_array).max()
    if peak > 0:
        audio_array = audio_array / peak * 0.95
    sf.write(buf, audio_array, sample_rate, format="OGG", subtype="VORBIS")
    return buf.getvalue()


async def _synthesize_vibevoice(text: str, speaker: Optional[str]) -> bytes:
    """Run VibeVoice inference (CPU, via transformers pipeline)."""
    loop = asyncio.get_event_loop()

    def _run():
        kwargs: dict = {}
        if speaker:
            kwargs["forward_params"] = {"speaker_embeddings": speaker}
        result = _tts_pipeline(text, **kwargs)
        audio = np.array(result["audio"])
        if audio.ndim > 1:
            audio = audio.squeeze()
        return _numpy_to_ogg(audio, result["sampling_rate"])

    return await loop.run_in_executor(None, _run)


async def _synthesize_edge_tts(text: str, language: str) -> bytes:
    """Fallback: edge-tts (Microsoft Azure TTS, free, no API key)."""
    import edge_tts

    # Pick voice by language
    voice_map = {
        "de": "de-DE-ConradNeural",
        "en": "en-US-GuyNeural",
        "fr": "fr-FR-HenriNeural",
        "es": "es-ES-AlvaroNeural",
        "it": "it-IT-DiegoNeural",
        "nl": "nl-NL-MaartenNeural",
        "pl": "pl-PL-MarekNeural",
        "pt": "pt-BR-AntonioNeural",
        "ru": "ru-RU-DmitryNeural",
        "ja": "ja-JP-KeitaNeural",
        "zh": "zh-CN-YunxiNeural",
    }
    voice = voice_map.get(language[:2], FALLBACK_VOICE)

    communicate = edge_tts.Communicate(text, voice)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    return buf.read()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "provider": _provider,
        "model": MODEL_NAME if _model_loaded else "edge-tts",
        "model_loaded": _model_loaded,
    }


@app.get("/voices")
async def list_voices():
    """Return available speakers (VibeVoice multi-speaker or edge-tts voices)."""
    if _model_loaded and _tts_pipeline:
        return {"provider": "vibevoice", "speakers": ["default"]}
    return {
        "provider": "edge-tts",
        "speakers": [
            "de-DE-ConradNeural", "de-DE-KatjaNeural",
            "en-US-GuyNeural", "en-US-JennyNeural",
            "fr-FR-HenriNeural", "es-ES-AlvaroNeural",
        ],
    }


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """Synthesize text to speech. Returns OGG audio bytes."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    if len(req.text) > 5000:
        raise HTTPException(status_code=400, detail="text too long (max 5000 chars)")

    t0 = time.time()
    try:
        if _model_loaded and _tts_pipeline:
            audio_bytes = await _synthesize_vibevoice(req.text, req.speaker)
            used = "vibevoice"
        else:
            audio_bytes = await _synthesize_edge_tts(req.text, req.language)
            used = "edge-tts"
    except Exception as e:
        logger.error(f"TTS error: {e}")
        # Last resort: try edge-tts even if we tried VibeVoice
        try:
            audio_bytes = await _synthesize_edge_tts(req.text, req.language)
            used = "edge-tts-fallback"
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"TTS failed: {e2}")

    elapsed = round(time.time() - t0, 2)
    logger.info(f"TTS [{used}] {len(req.text)} chars → {len(audio_bytes)} bytes in {elapsed}s")

    return Response(
        content=audio_bytes,
        media_type="audio/ogg",
        headers={
            "X-TTS-Provider": used,
            "X-TTS-Duration": str(elapsed),
        },
    )
