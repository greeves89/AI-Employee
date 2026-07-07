"""Meeting recording — transcription proxy over the local STT service.

Backend for the browser meeting-recording mode: the client records a real-world
meeting and streams audio chunks here; we transcribe each via the faster-whisper
STT service and return the text. Pure transcription — NO LLM turn, NO TTS. The
assembled transcript is then handed to a Meeting agent (see the "meeting-agent"
template) which produces a structured protocol + action items and stores it.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import settings
from app.dependencies import require_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/meetings", tags=["meetings"])


@router.post("/transcribe")
async def transcribe_chunk(
    file: UploadFile = File(...),
    user=Depends(require_auth),
):
    """Transcribe one recorded audio chunk via the local STT service.

    Returns ``{"text": "..."}``. Empty audio yields an empty string. The STT
    service (faster-whisper) is internal-only, so this authenticated proxy is the
    browser's entry point for meeting transcription."""
    raw = await file.read()
    if not raw:
        return {"text": ""}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{settings.stt_service_url}/transcribe",
                files={"file": (file.filename or "chunk.webm", raw, file.content_type or "audio/webm")},
            )
        resp.raise_for_status()
        return {"text": (resp.json().get("text") or "").strip()}
    except httpx.HTTPError as e:
        logger.warning("Meeting transcribe failed (STT service): %s", e)
        raise HTTPException(status_code=502, detail="Transkription fehlgeschlagen (STT-Service nicht erreichbar).")
    except Exception as e:  # noqa: BLE001 — surface a clean error, log the detail
        logger.warning("Meeting transcribe error: %s", e)
        raise HTTPException(status_code=500, detail="Transkription fehlgeschlagen.")
