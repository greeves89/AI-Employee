"""Meeting recording — transcription proxy with STT-service + OpenAI fallback.

Backend for the browser meeting-recording mode: the client records a real-world
meeting (in short segments) and streams each audio segment here; we transcribe it
and return the text. Pure transcription — NO LLM turn, NO TTS. The assembled
transcript is then handed to a Meeting agent (see the "meeting-agent" template)
which produces a structured protocol + action items and stores it.

Transcription order:
  1. Local faster-whisper STT service (``settings.stt_service_url``) — used on
     hosts that run it (e.g. a customer host).
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
import uuid

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.meeting import Meeting
from app.schemas.meeting import MeetingCreate, MeetingListResponse, MeetingResponse, MeetingUpdate
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

    # 1) Local STT service (any host that runs faster-whisper).
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


# ── Saved meetings ────────────────────────────────────────────────────────────
#
# Transcription above is stateless (no DB write) — the client used to hold the
# transcript only in memory and either discard it or fire it off as a one-shot
# chat message. Nothing was ever saved, so there was no history, no renaming,
# no participants. These endpoints are the actual save/list/edit surface;
# strictly scoped to the calling user (personal recordings, no agent/admin
# cross-visibility) — same fetch-then-check-owner shape as schedules.py.


async def _get_owned_meeting(meeting_id: str, user, db: AsyncSession) -> Meeting:
    meeting = await db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return meeting


@router.post("", response_model=MeetingResponse, status_code=201)
async def create_meeting(
    body: MeetingCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Save a finished recording — the actual persistence step for the
    recorder UI's "Speichern" action."""
    meeting = Meeting(
        id=uuid.uuid4().hex[:12],
        user_id=user.id,
        title=body.title,
        transcript=body.transcript,
        participants=body.participants,
        duration_seconds=body.duration_seconds,
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return meeting


@router.get("", response_model=MeetingListResponse)
async def list_meetings(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(Meeting.user_id == user.id).order_by(Meeting.created_at.desc())
    )
    meetings = list(result.scalars().all())
    return MeetingListResponse(meetings=meetings, total=len(meetings))


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    return await _get_owned_meeting(meeting_id, user, db)


@router.patch("/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
    meeting_id: str,
    body: MeetingUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Rename, edit the transcript, or edit the participants list."""
    meeting = await _get_owned_meeting(meeting_id, user, db)
    if body.title is not None:
        meeting.title = body.title
    if body.transcript is not None:
        meeting.transcript = body.transcript
    if body.participants is not None:
        meeting.participants = body.participants
    await db.commit()
    await db.refresh(meeting)
    return meeting


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_meeting(meeting_id, user, db)
    await db.execute(delete(Meeting).where(Meeting.id == meeting_id))
    await db.commit()
