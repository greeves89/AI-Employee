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

# --- Nova Sonic tools -------------------------------------------------------
# Fast tools answer directly from orchestrator data (DB/Redis, milliseconds).
# The slow ask_agent tool spins up a full agent turn in the container — only for
# real work that needs the agent's brain/tools.

GET_AGENT_STATUS_TOOL = {
    "toolSpec": {
        "name": "get_agent_status",
        "description": (
            "Get the agent's CURRENT status instantly: running/idle, what it is doing "
            "right now, and how many tasks are queued. Use for 'what are you doing', "
            "'what's your status'. Fast — reads live data directly, does NOT disturb the agent."
        ),
        "inputSchema": {"json": json.dumps({"type": "object", "properties": {}})},
    }
}

LIST_AGENT_TASKS_TOOL = {
    "toolSpec": {
        "name": "list_agent_tasks",
        "description": (
            "List the agent's recent tasks with their outcome (completed/failed/running) "
            "instantly. Use for 'what are your tasks', 'what did you do', 'what failed'. "
            "Fast — reads directly from the database, does NOT disturb the agent."
        ),
        "inputSchema": {"json": json.dumps({
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many, default 8."}},
        })},
    }
}

GET_AGENT_SETTINGS_TOOL = {
    "toolSpec": {
        "name": "get_agent_settings",
        "description": (
            "Read the agent's current settings instantly: model, mode/harness, provider, "
            "autonomy level, budget. Use for 'which model do you use', 'what's your setup'. "
            "Fast — reads directly, does NOT disturb the agent."
        ),
        "inputSchema": {"json": json.dumps({"type": "object", "properties": {}})},
    }
}

# Slow tool: hand real work to the container agent.
ASK_AGENT_TOOL = {
    "toolSpec": {
        "name": "ask_agent",
        "description": (
            "Delegate real WORK to the AI agent: writing/changing files, sending "
            "email/M365, running code, config changes, or anything that needs the agent "
            "to actually DO something or reason deeply. This takes a few seconds. Do NOT "
            "use it for status/task questions (use the fast tools) or smalltalk. IMPORTANT: "
            "say a short spoken filler FIRST (e.g. 'Moment, ich kümmere mich darum') so there "
            "is no silence while the agent works."
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
        f"Du sprichst {lang}, natürlich und knapp, wie am Telefon. Du BIST der Agent "
        "gegenüber dem Nutzer.\n"
        "TOOL-WAHL (wichtig für Tempo):\n"
        "• Fragen nach Status/Was-machst-du → get_agent_status (sofort).\n"
        "• Fragen nach Aufgaben/was lief/was fehlschlug → list_agent_tasks (sofort).\n"
        "• Fragen nach den Einstellungen (Modell, Modus, Autonomie) → get_agent_settings (sofort).\n"
        "Diese drei Tools antworten in Millisekunden — nutze sie IMMER für Daten-/Status-Fragen, "
        "statt den Agenten zu fragen.\n"
        "• Nur für echte ARBEIT (Dateien ändern, E-Mail, Code, komplexe Aufgaben) → ask_agent. "
        "Das dauert ein paar Sekunden — sag deshalb VORHER kurz einen Füller wie "
        "„Moment, ich kümmere mich darum“, damit keine Stille entsteht.\n"
        "Smalltalk, Begrüßungen und Rückfragen beantwortest du selbst ohne Tool. "
        "Halte gesprochene Antworten kurz — keine Aufzählungen, kein Code, sprich wie ein Mensch."
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
        cfg = (agent.config if agent else {}) or {}
        agent_role = cfg.get("role") or ""  # role lives in config, not on the ORM row

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
            tools=[
                GET_AGENT_STATUS_TOOL,
                LIST_AGENT_TASKS_TOOL,
                GET_AGENT_SETTINGS_TOOL,
                ASK_AGENT_TOOL,
            ],
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

    async def _respond(self, tool_use_id: str, text: str) -> None:
        if self._nova:
            await self._nova.send_tool_result(tool_use_id, text)

    async def _handle_tool_use(self, data: dict) -> None:
        tool_use_id = data.get("tool_use_id", "")
        name = data.get("name", "")
        raw = data.get("input", "") or ""
        try:
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                args = {}
        except (json.JSONDecodeError, TypeError):
            args = {}

        # ── Fast tools: read orchestrator data directly (ms, no agent round-trip) ──
        if name == "get_agent_status":
            await self._respond(tool_use_id, await self._fast_status())
            return
        if name == "list_agent_tasks":
            await self._respond(tool_use_id, await self._fast_tasks(int(args.get("limit") or 8)))
            return
        if name == "get_agent_settings":
            await self._respond(tool_use_id, await self._fast_settings())
            return

        # ── Slow tool: real work via the container agent ──
        if name != "ask_agent":
            await self._respond(tool_use_id, "Unbekanntes Tool.")
            return
        instruction = (args.get("instruction") or "").strip()
        if not instruction:
            await self._respond(tool_use_id, "Keine Instruktion erkannt.")
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
        await self._respond(tool_use_id, answer or "Der Agent hat keine Antwort geliefert.")

    # ── Fast direct-data readers (no agent round-trip) ──────────────

    async def _fast_status(self) -> str:
        from app.db.session import async_session_factory
        from app.models.agent import Agent
        from sqlalchemy import select
        state = "unbekannt"
        async with async_session_factory() as db:
            a = (await db.execute(select(Agent).where(Agent.id == self.agent_id))).scalar_one_or_none()
            if a:
                state = a.state.value if hasattr(a.state, "value") else str(a.state)
        current, qd = "", 0
        try:
            st = await self.redis.get_agent_status(self.agent_id)
            current = (st or {}).get("current_task") or ""
            qd = await self.redis.get_queue_depth(self.agent_id)
        except Exception:  # noqa: BLE001
            pass
        parts = [f"Status: {state}"]
        if current:
            parts.append(f"arbeitet gerade an „{current}“")
        parts.append(f"{qd} Aufgaben in der Warteschlange")
        return "; ".join(parts) + "."

    async def _fast_tasks(self, limit: int) -> str:
        from collections import Counter
        from app.db.session import async_session_factory
        from app.models.task import Task, TaskStatus
        from sqlalchemy import select
        limit = max(1, min(limit, 20))
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(Task).where(Task.agent_id == self.agent_id)
                .order_by(Task.created_at.desc()).limit(limit)
            )).scalars().all()
        if not rows:
            return "Keine Aufgaben für diesen Agenten gefunden."
        counts = Counter((t.status.value if hasattr(t.status, "value") else str(t.status)) for t in rows)
        summary = ", ".join(f"{n} {k}" for k, n in counts.items())
        lines = []
        for t in rows:
            s = t.status.value if hasattr(t.status, "value") else str(t.status)
            line = f"- {t.title} ({s})"
            if t.status == TaskStatus.FAILED and t.error:
                line += f": {t.error[:100]}"
            lines.append(line)
        return f"Letzte {len(rows)} Aufgaben ({summary}):\n" + "\n".join(lines)

    async def _fast_settings(self) -> str:
        from app.db.session import async_session_factory
        from app.models.agent import Agent
        from sqlalchemy import select
        async with async_session_factory() as db:
            a = (await db.execute(select(Agent).where(Agent.id == self.agent_id))).scalar_one_or_none()
            if not a:
                return "Agent nicht gefunden."
            cfg = a.config or {}
            budget = f"{a.budget_usd} USD/Monat" if a.budget_usd else "kein Limit"
            return (
                f"Modell: {a.model}; Modus/Harness: {a.mode}; "
                f"Provider: {cfg.get('model_provider', 'Standard')}; "
                f"Autonomie: {(a.autonomy_level or 'l3').upper()}; Budget: {budget}."
            )

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
