"""Azure OpenAI Realtime — realtime speech-to-speech voice front (non-AWS path).

A drop-in sibling of ``NovaSonicSession`` (see ``realtime_nova_sonic.py``): it
holds one bidirectional voice conversation open, but speaks the **OpenAI Realtime
WebSocket protocol** against Azure's ``/openai/v1/realtime`` GA surface (model
``gpt-realtime``) instead of the AWS Bedrock bidi stream.

It exposes the SAME interface + ``on_event`` contract as ``NovaSonicSession`` so
``RealtimeVoiceSession`` drives either engine unchanged:
  open() → stream send_audio() while reading events via on_event → answer
  tool_use with send_tool_result() → close().
on_event kinds emitted: ``audio`` {pcm}, ``text`` {text, role}, ``tool_use``
{tool_use_id, name, input}, ``interrupted`` {}, ``error`` {message}, ``done`` {}.

Audio: the browser streams 16 kHz PCM (the Nova live path); Azure Realtime wants
24 kHz pcm16, so we upsample on the way in. Azure streams 24 kHz PCM back, which
the frontend already plays at 24 kHz — no client change.

Protocol confirmed live against the SKBS Azure endpoint (session.created →
session.update → conversation.item.create → response.create → response.output_*).
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import uuid
from collections.abc import Awaitable, Callable

import websockets

logger = logging.getLogger(__name__)

FRONTEND_SAMPLE_RATE = 16000   # what the browser live path sends
AZURE_SAMPLE_RATE = 24000      # OpenAI Realtime pcm16 in/out
OUTPUT_SAMPLE_RATE = 24000     # what we emit (frontend plays at this rate)
DEFAULT_VOICE = "marin"        # gpt-realtime GA voice (warm, neutral)

# Event callback: (event_type, payload) -> awaitable. Same contract as Nova Sonic.
EventCallback = Callable[[str, dict], Awaitable[None]]


class AzureRealtimeSession:
    """One bidirectional Azure OpenAI Realtime (gpt-realtime) conversation."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str = "gpt-realtime",
        system_prompt: str,
        tools: list[dict] | None = None,
        voice_id: str = DEFAULT_VOICE,
        on_event: EventCallback,
        temperature: float = 0.7,
    ) -> None:
        self.endpoint = endpoint
        self._api_key = api_key
        self.model = model or "gpt-realtime"
        self.system_prompt = system_prompt
        # tools are expected in OpenAI function format:
        #   {"type":"function","name":..,"description":..,"parameters":{json schema}}
        self.tools = tools or []
        self.voice_id = voice_id or DEFAULT_VOICE
        self.on_event = on_event
        self.temperature = temperature

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._resample_state = None          # audioop.ratecv running state (16k→24k)
        # Accumulators for the current assistant turn's spoken transcript, so we
        # emit ONE final text per turn (like Nova) instead of per-delta spam.
        self._assistant_buf: dict[str, str] = {}

    # ── URL / connect ───────────────────────────────────────────────

    def _ws_url(self) -> str:
        url = self.endpoint.strip()
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        # endpoint already carries ?model=gpt-realtime; add it only if missing
        if "model=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}model={self.model}"
        return url

    async def open(self) -> None:
        """Connect, configure the session (audio in/out + tools), start receiving."""
        self._ws = await websockets.connect(
            self._ws_url(),
            additional_headers={"api-key": self._api_key},
            max_size=None,
            open_timeout=20,
            ping_interval=20,
            ping_timeout=20,
        )

        session: dict = {
            "type": "realtime",
            "instructions": self.system_prompt,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": AZURE_SAMPLE_RATE},
                    "turn_detection": {"type": "server_vad", "create_response": True},
                    "transcription": {"model": "whisper-1"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": AZURE_SAMPLE_RATE},
                    "voice": self.voice_id,
                },
            },
        }
        if self.tools:
            session["tools"] = self.tools
            session["tool_choice"] = "auto"
        await self._send({"type": "session.update", "session": session})

        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info("AzureRealtimeSession opened model=%s voice=%s tools=%d",
                    self.model, self.voice_id, len(self.tools))

    async def _send(self, event: dict) -> None:
        if self._closed or self._ws is None:
            return
        await self._ws.send(json.dumps(event))

    # ── inbound audio ───────────────────────────────────────────────

    async def send_audio(self, pcm_16k: bytes) -> None:
        """Upsample 16 kHz browser PCM to 24 kHz and append to the input buffer."""
        if self._closed or self._ws is None or not pcm_16k:
            return
        pcm_24k, self._resample_state = audioop.ratecv(
            pcm_16k, 2, 1, FRONTEND_SAMPLE_RATE, AZURE_SAMPLE_RATE, self._resample_state
        )
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm_24k).decode("ascii"),
        })

    # ── proactive text injection (async delegation report) ──────────

    async def inject_user_text(self, text: str) -> None:
        """Inject a user text turn mid-session so the model speaks it proactively."""
        if self._closed:
            return
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }})
        await self._send({"type": "response.create"})

    # ── tool result ─────────────────────────────────────────────────

    async def send_tool_result(self, tool_use_id: str, result: str) -> None:
        """Feed a function_call_output back so the model incorporates + speaks it."""
        if self._closed:
            return
        await self._send({"type": "conversation.item.create", "item": {
            "type": "function_call_output",
            "call_id": tool_use_id,
            "output": result,
        }})
        await self._send({"type": "response.create"})

    # ── receive loop ────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        try:
            assert self._ws is not None
            async for raw in self._ws:
                if self._closed:
                    break
                try:
                    evt = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                await self._dispatch(evt.get("type", ""), evt)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if not self._closed:
                logger.warning("AzureRealtime receive loop error: %s", e, exc_info=True)
                await self._safe_emit("error", {"message": str(e)})
        finally:
            await self._safe_emit("done", {})

    async def _dispatch(self, kind: str, evt: dict) -> None:
        # Spoken audio out (24 kHz pcm16, base64 in "delta").
        if kind in ("response.output_audio.delta", "response.audio.delta"):
            pcm = base64.b64decode(evt.get("delta", "") or "")
            if pcm:
                await self._safe_emit("audio", {"pcm": pcm})
        # Assistant transcript: accumulate deltas, emit once on done (matches Nova's
        # one-text-per-turn behaviour, avoids per-token UI spam).
        elif kind in ("response.output_audio_transcript.delta", "response.output_text.delta"):
            rid = evt.get("response_id", "") or evt.get("item_id", "") or "_"
            self._assistant_buf[rid] = self._assistant_buf.get(rid, "") + str(evt.get("delta", ""))
        elif kind in ("response.output_audio_transcript.done", "response.output_text.done"):
            rid = evt.get("response_id", "") or evt.get("item_id", "") or "_"
            text = evt.get("transcript") or evt.get("text") or self._assistant_buf.pop(rid, "")
            if text:
                await self._safe_emit("text", {"text": text, "role": "ASSISTANT"})
        # User transcript (server-side STT of the mic).
        elif kind == "conversation.item.input_audio_transcription.completed":
            t = str(evt.get("transcript", "") or "").strip()
            if t:
                await self._safe_emit("text", {"text": t, "role": "USER"})
        # Barge-in: the user started speaking over the assistant.
        elif kind == "input_audio_buffer.speech_started":
            await self._safe_emit("interrupted", {})
        # Tool / function call requested by the model.
        elif kind == "response.function_call_arguments.done":
            await self._safe_emit("tool_use", {
                "tool_use_id": evt.get("call_id", "") or evt.get("id", ""),
                "name": evt.get("name", ""),
                "input": evt.get("arguments", "") or "",
            })
        elif kind == "error":
            err = evt.get("error", evt) or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            logger.warning("AzureRealtime server error: %s", json.dumps(evt)[:400])
            await self._safe_emit("error", {"message": msg or "Realtime error"})
        # response.done / session.* / *.added / *.part.* : lifecycle only.

    async def _safe_emit(self, kind: str, data: dict) -> None:
        try:
            await self.on_event(kind, data)
        except Exception:  # noqa: BLE001
            logger.warning("AzureRealtime on_event(%s) handler failed", kind, exc_info=True)

    # ── teardown ────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._ws is not None:
                await self._ws.close()
        except Exception:  # noqa: BLE001
            logger.debug("AzureRealtime close cleanup error", exc_info=True)
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass


def tools_novaspec_to_openai(tools: list[dict] | None) -> list[dict]:
    """Convert the Nova Sonic ``toolSpec`` tool list into OpenAI function format.

    Nova:   {"toolSpec": {"name","description","inputSchema": {"json": "<json str>"}}}
    OpenAI: {"type":"function","name","description","parameters": {<json object>}}
    """
    out: list[dict] = []
    for t in tools or []:
        spec = t.get("toolSpec") if isinstance(t, dict) else None
        if not isinstance(spec, dict):
            continue
        params: dict = {"type": "object", "properties": {}}
        schema = (spec.get("inputSchema") or {}).get("json")
        if isinstance(schema, str) and schema.strip():
            try:
                params = json.loads(schema)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(schema, dict):
            params = schema
        out.append({
            "type": "function",
            "name": spec.get("name", ""),
            "description": spec.get("description", ""),
            "parameters": params,
        })
    return out
