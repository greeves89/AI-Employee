"""Meeting recording — transcription proxy with STT-service + OpenAI fallback.

Backend for the browser meeting-recording mode: the client records a real-world
meeting (in short segments) and streams each audio segment here; we transcribe it
and return the text. Pure transcription — NO LLM turn, NO TTS. The assembled
transcript is then handed to a Meeting agent (see the "meeting-agent" template)
which produces a structured protocol + action items and stores it.

Transcription order:
  1. Local faster-whisper STT service (``settings.stt_service_url``) — used on
     hosts that run it (e.g. SKBS).
  2. Fallback: OpenAI Whisper (``/v1/audio/transcriptions``) — for hosts WITHOUT
     a local STT service (e.g. the Pi). Key from the platform setting
     ``voice_openai_api_key`` or the ``OPENAI_API_KEY`` env.

Long meetings are handled by the client, which records in short segments (~60s)
and posts each one here — so a single request never carries the whole meeting.
That keeps every request small (well under OpenAI's 25 MB limit), avoids the
STT timeout on long audio, and means an interrupted recording never loses the
already-transcribed part (it is appended live to the transcript).
"""
import logging

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import require_auth
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/meetings", tags=["meetings"])

# OpenAI Whisper rejects files > 25 MB. The client segments recordings so this is
# never hit in normal use; we guard anyway to return a clear error, not a 400.
_OPENAI_MAX_BYTES = 24 * 1024 * 1024


async def _transcribe_local(raw: bytes, filename: str, content_type: str) -> str:
    """Transcribe via the local faster-whisper STT service. Raises on failure."""
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.stt_service_url}/transcribe",
            files={"file": (filename, raw, content_type)},
        )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


async def _transcribe_openai(raw: bytes, filename: str, content_type: str, api_key: str) -> str:
    """Transcribe via OpenAI Whisper. Raises on failure."""
    if len(raw) > _OPENAI_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio-Segment zu groß für den OpenAI-Fallback (max. 25 MB). "
                   "Bitte in kürzeren Abschnitten aufnehmen.",
        )
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, raw, content_type)},
            data={"model": "whisper-1", "response_format": "json"},
        )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


@router.post("/transcribe")
async def transcribe_chunk(
    file: UploadFile = File(...),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Transcribe one recorded audio segment.

    Tries the local STT service first; if it is not configured/reachable, falls
    back to OpenAI Whisper. Returns ``{"text": "..."}``; empty audio yields an
    empty string."""
    raw = await file.read()
    if not raw:
        return {"text": ""}
    filename = file.filename or "chunk.webm"
    content_type = file.content_type or "audio/webm"

    # 1) Local STT service (SKBS and any host that runs faster-whisper).
    try:
        return {"text": await _transcribe_local(raw, filename, content_type)}
    except httpx.HTTPError as e:
        logger.info("Local STT unavailable, trying OpenAI fallback: %s", e)

    # 2) Fallback: OpenAI Whisper (hosts without a local STT service, e.g. Pi).
    api_key = (await SettingsService(db).get("voice_openai_api_key")) or settings.openai_api_key
    if not api_key:
        raise HTTPException(
            status_code=502,
            detail="Transkription fehlgeschlagen: kein STT-Service erreichbar und kein "
                   "OpenAI-Key für den Fallback konfiguriert (Einstellungen → voice_openai_api_key).",
        )
    try:
        return {"text": await _transcribe_openai(raw, filename, content_type, api_key)}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.warning("OpenAI transcription failed: %s %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(status_code=502, detail="Transkription über den OpenAI-Fallback fehlgeschlagen.")
    except Exception as e:  # noqa: BLE001 — surface a clean error, log the detail
        logger.warning("Meeting transcribe error (fallback): %s", e)
        raise HTTPException(status_code=500, detail="Transkription fehlgeschlagen.")
