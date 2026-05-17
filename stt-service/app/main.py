"""STT Service — faster-whisper local speech-to-text. Free, offline, no API key.

POST /transcribe   (multipart: file, language?)  → { text, language, duration }
GET  /healthz      → { status, model, model_loaded }

Used by the orchestrator to transcribe Telegram voice messages before they
reach the agent — the agent receives plain text, never raw audio.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logger = logging.getLogger("stt-service")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AI-Employee STT Service", version="1.0.0")

# "small" is a good German quality/speed tradeoff (~480 MB). Override via env.
MODEL_SIZE = os.getenv("STT_MODEL", "small")
MODEL_DIR = os.getenv("STT_MODEL_DIR", "/models")
MAX_AUDIO_BYTES = 25 * 1024 * 1024

_model = None


def _get_model():
    """Lazily load the Whisper model (downloads on first use)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading faster-whisper model '%s' (cpu/int8)…", MODEL_SIZE)
        _model = WhisperModel(
            MODEL_SIZE, device="cpu", compute_type="int8", download_root=MODEL_DIR
        )
        logger.info("Model '%s' ready", MODEL_SIZE)
    return _model


def _transcribe_sync(path: str, language: str | None) -> dict:
    model = _get_model()
    segments, info = model.transcribe(
        path, language=language or None, vad_filter=True
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return {
        "text": text,
        "language": info.language,
        "duration": round(info.duration, 2),
    }


@app.on_event("startup")
async def startup():
    # Warm-load in the background so the first request isn't slow.
    async def _warm():
        try:
            await asyncio.get_event_loop().run_in_executor(None, _get_model)
        except Exception as e:  # noqa: BLE001
            logger.warning("Model warm-load failed (will retry on request): %s", e)

    asyncio.create_task(_warm())
    logger.info("STT Service starting — model=%s", MODEL_SIZE)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "model": MODEL_SIZE, "model_loaded": _model is not None}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio file")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio too large (max 25 MB)")

    suffix = os.path.splitext(file.filename or "audio.ogg")[1] or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name

    t0 = time.time()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _transcribe_sync, path, language
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("transcription failed")
        raise HTTPException(status_code=500, detail=f"transcription failed: {e}")
    finally:
        if os.path.exists(path):
            os.unlink(path)

    elapsed = round(time.time() - t0, 2)
    logger.info(
        "Transcribed %d bytes → %d chars in %ss (lang=%s)",
        len(data), len(result["text"]), elapsed, result["language"],
    )
    return result
