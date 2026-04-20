import io
import json
import logging
import tempfile

import httpx
import redis.asyncio as aioredis
from telegram import Update
from telegram.ext import ContextTypes

from app.config import settings
from app.telegram.handlers.commands import _active_chats

logger = logging.getLogger(__name__)


def _get_openai_key() -> str:
    return settings.openai_api_key


async def _transcribe_voice(file_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe audio bytes via OpenAI Whisper API."""
    api_key = _get_openai_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, io.BytesIO(file_bytes), "audio/ogg")},
            data={"model": "whisper-1", "language": "de"},
        )
        resp.raise_for_status()
        return resp.json()["text"]


async def _text_to_speech(text: str, language: str = "de") -> bytes:
    """Convert text to speech via local TTS service (VibeVoice / edge-tts fallback).

    Falls back to OpenAI TTS if the local service is unreachable.
    """
    tts_url = settings.tts_service_url
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{tts_url}/synthesize",
                json={"text": text, "language": language},
            )
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.warning(f"Local TTS service unavailable ({e}), falling back to OpenAI TTS")

    # Fallback: OpenAI TTS (requires API key)
    api_key = _get_openai_key()
    if not api_key:
        raise RuntimeError("TTS service unreachable and OPENAI_API_KEY not set")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini-tts", "input": text, "voice": "marin", "response_format": "opus"},
        )
        resp.raise_for_status()
        return resp.content


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages: transcribe via Whisper and forward to active agent."""
    chat_id = update.effective_chat.id
    voice = update.message.voice or update.message.audio

    if not voice:
        return

    if not _get_openai_key():
        await update.message.reply_text(
            "Spracherkennung nicht verfugbar. OPENAI_API_KEY fehlt."
        )
        return

    # Show recording indicator
    await update.effective_chat.send_action("record_voice")

    try:
        # Download voice file from Telegram
        tg_file = await voice.get_file()
        file_bytes = bytes(await tg_file.download_as_bytearray())

        # Transcribe via Whisper
        transcript = await _transcribe_voice(file_bytes)

        if not transcript.strip():
            await update.message.reply_text(
                "Ich konnte die Sprachnachricht nicht verstehen."
            )
            return

        # Show what was understood
        await update.message.reply_text(
            f"Ich habe verstanden: _{transcript}_", parse_mode="Markdown"
        )

        if chat_id not in _active_chats:
            await update.message.reply_text(
                "Kein aktiver Chat. Starte mit /chat einen Chat mit einem Agent."
            )
            return

        agent_id = _active_chats[chat_id]

        # Forward transcribed text to agent via Redis (same as handle_message)
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        message_id = f"tg-voice-{update.message.message_id}"
        payload = json.dumps({
            "id": message_id,
            "text": transcript,
            "model": None,
        })
        await redis.lpush(f"agent:{agent_id}:chat", payload)
        await redis.aclose()

        # Show typing indicator while agent processes
        await update.effective_chat.send_action("typing")

    except Exception as e:
        await update.message.reply_text(f"Fehler bei der Spracherkennung: {e}")
