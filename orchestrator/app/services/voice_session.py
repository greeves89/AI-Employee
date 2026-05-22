"""VoiceSession — one live voice conversation between user and an agent.

Pipeline per user turn:
  audio chunks → STT → transcript
                     ↓
              [Container Agent]  (via Redis chat queue, same as text chat)
                     ↓
              response text
                     ↓
              [optional Haiku reformatter for voice-friendly output]
                     ↓
              TTS chunks → client

The "interaction agent" is the orchestrator-side dispatcher in this class:
it speaks for the agent (using Haiku for quick acks + voice reformatting)
and delegates the actual work to the container agent through the existing
Redis-backed chat infrastructure.

Barge-in: if a new audio commit arrives while TTS is streaming, the current
TTS task is cancelled. The container agent task continues (we don't cancel
the worker — the user can still get the text response in the chat panel).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.redis_service import RedisService
from app.services.voice_providers import get_llm, get_stt, get_tts
from app.services.voice_providers.base import (
    STTProvider, TTSProvider, VoiceLLMProvider,
)
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)


# Threshold above which we ask Haiku to summarise the container output
# before reading it aloud. Short answers go to TTS verbatim.
VOICE_REFORMAT_THRESHOLD = 400

VOICE_REFORMAT_SYSTEM = (
    "Du bist die Sprach-Frontend eines KI-Agenten. Der Agent hat gerade "
    "diese Antwort produziert. Fasse sie in 1–3 kurzen Sätzen zusammen, "
    "in gesprochener Sprache, OHNE Code, OHNE Aufzählungen, OHNE Markdown. "
    "Wenn die Antwort kurz und natürlich ist, gib sie wortgleich zurück. "
    "Antworte nur mit dem zu sprechenden Text — keine Meta-Kommentare."
)

QUICK_ACK_SYSTEM = (
    "Du bist die Sprach-Frontend eines KI-Agenten. Der User hat dich gerade "
    "um folgendes gebeten. Antworte EIN kurzes Acknowledgement (max. 6 Wörter, "
    "deutsch, locker), das andeutet, dass du die Aufgabe in Angriff nimmst. "
    "Z.B. 'Klar, schau ich nach.' oder 'Moment, mach ich.' "
    "Antworte nur mit dem Ack-Satz, sonst nichts."
)


@dataclass
class VoiceSession:
    agent_id: str
    user_id: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Injected dependencies
    redis: RedisService = field(default=None)  # type: ignore[assignment]
    db_factory: callable = None  # async_session_factory

    # Provider instances (loaded per-turn so settings changes take effect live)
    _stt: STTProvider | None = None
    _tts: TTSProvider | None = None
    _llm: VoiceLLMProvider | None = None

    # Inbound audio buffer (raw bytes — usually webm/opus from the browser)
    _audio_buf: bytearray = field(default_factory=bytearray)

    # The currently active TTS task — cancellable for barge-in
    _tts_task: asyncio.Task | None = None

    # The async iterator that streams response audio out to the caller
    _out_queue: asyncio.Queue | None = None

    async def init(self, db: AsyncSession) -> None:
        """Load active providers from settings. Called once per session."""
        self._stt = await get_stt(db)
        self._tts = await get_tts(db)
        self._llm = await get_llm(db)
        self._out_queue = asyncio.Queue(maxsize=128)
        logger.info(
            "VoiceSession init agent=%s user=%s stt=%s tts=%s llm=%s",
            self.agent_id, self.user_id,
            self._stt.name, self._tts.name, self._llm.name,
        )

    # ── Inbound: audio from the browser ──────────────────────────

    def push_audio_chunk(self, data: bytes) -> None:
        self._audio_buf.extend(data)
        logger.warning(
            "VoiceSession audio chunk agent=%s session=%s chunk=%d total=%d",
            self.agent_id, self.session_id, len(data), len(self._audio_buf),
        )

    def reset_audio_buffer(self) -> bytes:
        data = bytes(self._audio_buf)
        self._audio_buf.clear()
        return data

    async def interrupt(self) -> None:
        """Barge-in: cancel current TTS stream."""
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
            try:
                await self._tts_task
            except (asyncio.CancelledError, Exception):
                pass
        # Drain any queued audio chunks so the client stops hearing the old turn
        if self._out_queue:
            while not self._out_queue.empty():
                try:
                    self._out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    # ── Outbound: audio + events to the browser ───────────────────

    async def outbound(self) -> AsyncIterator[dict]:
        """Yields events to send over the WebSocket to the client."""
        assert self._out_queue is not None
        while True:
            evt = await self._out_queue.get()
            if evt is None:  # sentinel
                break
            yield evt

    async def _emit(self, event: dict) -> None:
        if self._out_queue:
            await self._out_queue.put(event)

    # ── Turn handling ────────────────────────────────────────────

    async def commit_turn(self, language: str | None = None) -> None:
        """User finished speaking. Transcribe → delegate → speak response."""
        audio = self.reset_audio_buffer()
        logger.warning(
            "VoiceSession commit agent=%s session=%s audio=%d language=%s",
            self.agent_id, self.session_id, len(audio), language,
        )
        if not audio:
            await self._emit({"type": "error", "data": {"message": "no audio"}})
            return

        # 1. Transcribe
        try:
            await self._emit({"type": "status", "data": {"message": "Transkribiere Audio..."}})
            logger.warning(
                "VoiceSession STT start agent=%s session=%s provider=%s audio=%d",
                self.agent_id,
                self.session_id,
                getattr(self._stt, "name", "unknown"),
                len(audio),
            )
            transcript = await asyncio.wait_for(
                self._stt.transcribe(audio, language=language),  # type: ignore[union-attr]
                timeout=35.0,
            )
            logger.warning(
                "VoiceSession STT done agent=%s session=%s chars=%d",
                self.agent_id,
                self.session_id,
                len(transcript),
            )
        except asyncio.TimeoutError:
            logger.warning("STT timed out agent=%s session=%s", self.agent_id, self.session_id)
            await self._emit({"type": "error", "data": {"message": "Transkription hat zu lange gedauert."}})
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("STT failed")
            await self._emit({"type": "error", "data": {"message": f"STT failed: {e}"}})
            return

        if not transcript:
            logger.warning("VoiceSession STT empty agent=%s session=%s", self.agent_id, self.session_id)
            await self._emit({"type": "error", "data": {"message": "Ich konnte die Aufnahme nicht verstehen."}})
            return

        await self._emit({"type": "transcript", "data": {"text": transcript}})

        # 2. Quick spoken ack while the container processes (parallel)
        ack_task = asyncio.create_task(self._speak_quick_ack(transcript))

        # 3. Delegate to container agent via existing Redis chat queue
        await self._emit({"type": "status", "data": {"message": "Frage Agent..."}})
        response_text = await self._delegate_to_container(transcript)
        await ack_task  # ensure ack finished speaking before final answer

        if not response_text:
            await self._emit({"type": "error", "data": {"message": "Agent timeout"}})
            return

        await self._emit({"type": "response", "data": {"text": response_text}})

        # 4. Reformat for voice if too long / contains code
        spoken = await self._make_spoken(response_text)

        # 5. TTS streaming (cancellable for barge-in)
        await self._emit({"type": "status", "data": {"message": "Erzeuge Sprachantwort..."}})
        self._tts_task = asyncio.create_task(self._stream_tts(spoken))
        try:
            await self._tts_task
        except asyncio.CancelledError:
            pass

        await self._emit({"type": "done", "data": {}})

    async def _speak_quick_ack(self, transcript: str) -> None:
        """Generate a 1-sentence ack via Haiku and stream it to TTS."""
        try:
            chunks: list[str] = []
            async for delta in self._llm.stream_response(  # type: ignore[union-attr]
                messages=[{"role": "user", "content": transcript}],
                system_prompt=QUICK_ACK_SYSTEM,
            ):
                chunks.append(delta)
            ack = "".join(chunks).strip().strip('"')
            if ack:
                await self._stream_tts(ack, tag="ack")
        except Exception:  # noqa: BLE001
            logger.warning("ack generation failed", exc_info=True)

    async def _delegate_to_container(self, transcript: str, timeout: float = 90.0) -> str:
        """Push the transcript to the agent's chat queue, await the 'done' event.

        Mirrors the WS chat handler: publishes user message to
        agent:{id}:chat and subscribes to agent:{id}:chat:response,
        collecting the streamed text until 'done'.
        """
        assert self.redis and self.redis.client
        message_id = uuid.uuid4().hex[:12]
        chat_payload = json.dumps({
            "id": message_id,
            "text": transcript,
            "model": None,
            "images": [],
            "source": "webapp_voice",
        })
        channel = f"agent:{self.agent_id}:chat:response"
        pubsub = await self.redis.subscribe(channel)
        await self.redis.client.lpush(f"agent:{self.agent_id}:chat", chat_payload)

        collected: list[str] = []
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            while asyncio.get_event_loop().time() < deadline:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if not msg or msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                try:
                    evt = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if evt.get("message_id") != message_id:
                    continue
                etype = evt.get("type")
                edata = evt.get("data") or {}
                if etype == "text":
                    collected.append(str(edata.get("text", "")))
                elif etype == "done":
                    break
                elif etype == "error":
                    return f"[Fehler: {edata.get('message', 'unbekannt')}]"
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        return "".join(collected).strip()

    async def _make_spoken(self, text: str) -> str:
        """If response is short and natural, speak verbatim. Else summarise via Haiku."""
        if len(text) <= VOICE_REFORMAT_THRESHOLD and "```" not in text:
            return text
        try:
            chunks: list[str] = []
            async for delta in self._llm.stream_response(  # type: ignore[union-attr]
                messages=[{"role": "user", "content": text}],
                system_prompt=VOICE_REFORMAT_SYSTEM,
            ):
                chunks.append(delta)
            return "".join(chunks).strip() or text
        except Exception:  # noqa: BLE001
            logger.warning("voice reformatter failed, falling back to raw text")
            return text[:600]  # last-resort truncation so TTS doesn't run forever

    async def _stream_tts(self, text: str, tag: str = "main") -> None:
        """Synthesize text → emit audio chunks to client."""
        if not text:
            return
        await self._emit({"type": "tts_start", "data": {"tag": tag}})
        try:
            async for chunk in self._tts.synthesize(text):  # type: ignore[union-attr]
                if not chunk:
                    continue
                import base64
                await self._emit({
                    "type": "audio_chunk",
                    "data": {
                        "tag": tag,
                        "mime": self._tts.output_mime,  # type: ignore[union-attr]
                        "b64": base64.b64encode(chunk).decode("ascii"),
                    },
                })
        except asyncio.CancelledError:
            await self._emit({"type": "tts_cancelled", "data": {"tag": tag}})
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("TTS streaming failed")
            await self._emit({"type": "error", "data": {"message": f"TTS failed: {e}"}})
        finally:
            await self._emit({"type": "tts_end", "data": {"tag": tag}})

    async def close(self) -> None:
        await self.interrupt()
        if self._out_queue:
            await self._out_queue.put(None)
