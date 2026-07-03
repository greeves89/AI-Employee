"""RealtimeVoiceSession — a live Nova Sonic speech-to-speech front for one agent.

Drop-in alternative to ``VoiceSession`` (same interface the voice WS route uses:
``init`` / ``outbound`` / ``push_audio_chunk`` / ``commit_turn`` / ``interrupt`` /
``close``), selected when the agent is configured with a realtime interaction
model (``agent.config["interaction_model"] == "nova_sonic"``).

Bridge:
  browser 16 kHz PCM ──▶ Nova Sonic (cloud)
  Nova Sonic 24 kHz PCM ──▶ browser
  Nova Sonic ``ask_agent`` tool ──▶ ask_agent_via_chat() ──▶ container agent
                                     agent answer ──▶ tool result ──▶ spoken

Nova Sonic handles VAD/turn-taking itself, so there is no push-to-talk
``commit`` — audio streams continuously and the model decides when to speak.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.redis_service import RedisService
from app.services.agent_chat_bridge import ask_agent_via_chat
from app.services.settings_service import SettingsService
from app.services.voice_providers.realtime_nova_sonic import (
    NovaSonicSession,
    credentials_from_env,
)

logger = logging.getLogger(__name__)

# Tool that lets Nova Sonic hand real work to the container agent.
ASK_AGENT_TOOL = {
    "toolSpec": {
        "name": "ask_agent",
        "description": (
            "Delegate a task or question to the AI agent that does the real work: "
            "reading or writing files, email/M365/calendar, running code, looking up "
            "the user's data, web tasks. Call this whenever the user wants something "
            "DONE or asks about their files, data, projects or accounts. Do NOT call it "
            "for pure smalltalk, greetings or clarifying questions — answer those yourself."
        ),
        "inputSchema": {"json": json.dumps({
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "The task for the agent, phrased clearly in the user's language.",
                }
            },
            "required": ["instruction"],
        })},
    }
}


def _system_prompt(agent_name: str, agent_role: str, language: str) -> str:
    lang = "Deutsch" if (language or "de").startswith("de") else language
    role = f" Deine Rolle: {agent_role}." if agent_role else ""
    return (
        f"Du bist die Sprach-Front des KI-Agenten „{agent_name}“.{role} "
        f"Du sprichst {lang}, natürlich und knapp, wie am Telefon. "
        "Du BIST der Agent gegenüber dem Nutzer. Wenn der Nutzer etwas erledigt haben "
        "will oder nach seinen Dateien, Daten, Projekten, E-Mails oder Aufgaben fragt, "
        "rufe das Tool ask_agent mit einer klaren Instruktion auf und lies dann dessen "
        "Ergebnis vor. Smalltalk, Begrüßungen und Rückfragen beantwortest du selbst, "
        "ohne das Tool. Halte gesprochene Antworten kurz — keine Aufzählungen, kein Code."
    )


@dataclass
class RealtimeVoiceSession:
    agent_id: str
    user_id: str
    redis: RedisService
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    _nova: NovaSonicSession | None = None
    _out_queue: asyncio.Queue | None = None
    _in_queue: asyncio.Queue | None = None
    _pump_task: asyncio.Task | None = None
    _closed: bool = False

    # ── setup ───────────────────────────────────────────────────────

    async def init(self, db: AsyncSession) -> None:
        creds = credentials_from_env()
        if not creds:
            raise RuntimeError(
                "Nova Sonic realtime is selected for this agent but no AWS credentials "
                "are configured (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)."
            )

        from app.models.agent import Agent
        agent = (await db.execute(select(Agent).where(Agent.id == self.agent_id))).scalar_one_or_none()
        agent_name = agent.name if agent else self.agent_id
        agent_role = (agent.role if agent else "") or ""
        cfg = (agent.config if agent else {}) or {}

        svc = SettingsService(db)
        language = (await svc.get("voice_language")) or "de"
        voice_id = cfg.get("interaction_voice") or (await svc.get("nova_sonic_voice")) or "matthew"

        self._out_queue = asyncio.Queue(maxsize=512)
        self._in_queue = asyncio.Queue(maxsize=512)
        self._nova = NovaSonicSession(
            region=creds["region"],
            access_key=creds["access_key"],
            secret_key=creds["secret_key"],
            session_token=creds["session_token"],
            system_prompt=_system_prompt(agent_name, agent_role, language),
            tools=[ASK_AGENT_TOOL],
            voice_id=voice_id,
            on_event=self._on_nova_event,
        )
        await self._nova.open()
        self._pump_task = asyncio.create_task(self._audio_pump())
        logger.info(
            "RealtimeVoiceSession init agent=%s user=%s region=%s voice=%s",
            self.agent_id, self.user_id, creds["region"], voice_id,
        )

    # ── inbound audio (browser → Nova Sonic) ────────────────────────

    def push_audio_chunk(self, data: bytes) -> None:
        """16 kHz/16-bit/mono PCM from the browser. Queued for ordered delivery."""
        if self._closed or not self._in_queue:
            return
        try:
            self._in_queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("RealtimeVoiceSession inbound audio queue full agent=%s", self.agent_id)

    async def _audio_pump(self) -> None:
        assert self._in_queue is not None
        try:
            while not self._closed:
                chunk = await self._in_queue.get()
                if chunk is None:
                    break
                if self._nova:
                    await self._nova.send_audio(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("RealtimeVoiceSession audio pump error agent=%s", self.agent_id, exc_info=True)

    async def commit_turn(self, language: str | None = None) -> None:
        """No-op: Nova Sonic detects end-of-turn itself (VAD). Kept for interface parity."""
        return

    async def interrupt(self) -> None:
        """Barge-in. Nova Sonic handles interruption server-side; tell the client to
        drop any buffered audio so the old turn stops immediately."""
        await self._emit({"type": "clear_audio", "data": {}})

    # ── outbound (Nova Sonic → browser) ─────────────────────────────

    async def outbound(self) -> AsyncIterator[dict]:
        assert self._out_queue is not None
        while True:
            evt = await self._out_queue.get()
            if evt is None:
                break
            yield evt

    async def _emit(self, event: dict | None) -> None:
        if self._out_queue:
            await self._out_queue.put(event)

    # ── Nova Sonic events ───────────────────────────────────────────

    async def _on_nova_event(self, kind: str, data: dict) -> None:
        if kind == "audio":
            b64 = base64.b64encode(data.get("pcm", b"")).decode("ascii")
            await self._emit({"type": "audio_chunk", "data": {
                "b64": b64, "mime": "audio/pcm", "rate": 24000, "tag": "main",
            }})
        elif kind == "text":
            role = (data.get("role") or "").upper()
            text = data.get("text", "")
            if not text:
                return
            if role == "USER":
                await self._emit({"type": "transcript", "data": {"text": text}})
            else:  # ASSISTANT / other
                await self._emit({"type": "response", "data": {"text": text}})
        elif kind == "tool_use":
            # Run delegation without blocking the receive loop.
            asyncio.create_task(self._handle_tool_use(data))
        elif kind == "error":
            await self._emit({"type": "error", "data": {"message": data.get("message", "Realtime-Fehler")}})
        elif kind == "done":
            await self._emit({"type": "done", "data": {}})
            await self._emit(None)  # end the outbound stream

    async def _handle_tool_use(self, data: dict) -> None:
        tool_use_id = data.get("tool_use_id", "")
        name = data.get("name", "")
        raw = data.get("input", "") or ""
        if name != "ask_agent":
            if self._nova:
                await self._nova.send_tool_result(tool_use_id, "Unbekanntes Tool.")
            return
        try:
            instruction = json.loads(raw).get("instruction", "") if raw else ""
        except (json.JSONDecodeError, TypeError, AttributeError):
            instruction = raw
        instruction = (instruction or "").strip()
        if not instruction:
            if self._nova:
                await self._nova.send_tool_result(tool_use_id, "Keine Instruktion erkannt.")
            return

        await self._emit({"type": "status", "data": {"message": "Agent arbeitet…"}})
        await self._emit({"type": "delegate", "data": {"instruction": instruction}})
        try:
            answer = await ask_agent_via_chat(
                self.redis, self.agent_id, instruction, source="realtime_voice", timeout=120.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("realtime delegation failed agent=%s: %s", self.agent_id, e, exc_info=True)
            answer = "Der Agent konnte die Aufgabe gerade nicht bearbeiten."
        if self._nova:
            await self._nova.send_tool_result(tool_use_id, answer or "Der Agent hat keine Antwort geliefert.")

    # ── teardown ────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._in_queue:
            try:
                self._in_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if self._nova:
            await self._nova.close()
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._emit(None)
