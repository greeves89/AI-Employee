"""AWS Bedrock Nova Sonic — realtime speech-to-speech voice front.

Unlike the staged STT→LLM→TTS pipeline (see ``voice_session.py``), Nova Sonic is
a single bidirectional audio model: raw mic PCM streams in, spoken PCM streams
out, and the model itself handles turn-taking (VAD), transcription and speech.

The model runs in the AWS cloud — the orchestrator only holds the bidirectional
stream open, so there is **zero local inference load** (ideal for the Pi, where a
local speech model would cook the CPU).

Delegation to the container agent happens through a **tool call**: the prompt
declares an ``ask_agent`` tool; when Nova Sonic decides the user wants real work
done, it emits a ``toolUse`` event, we run the existing chat-delegation to the
agent container, and feed the agent's answer back as a ``toolResult`` — which
Nova Sonic then speaks. Simple conversation (greetings, clarifications) it
answers itself.

Audio formats (Nova Sonic v2 fixed spec):
  - input : 16 kHz, 16-bit, mono LPCM, base64
  - output: 24 kHz, 16-bit, mono LPCM, base64

SDK: ``aws-sdk-bedrock-runtime`` (Smithy async client) — plain boto3 has no
bidirectional streaming. Verified working on ARM (Raspberry Pi).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

MODEL_ID = "amazon.nova-2-sonic-v1:0"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

# Event callback: (event_type, payload) -> awaitable.
# event_type ∈ {"audio", "text", "tool_use", "usage", "error", "done"}
EventCallback = Callable[[str, dict], Awaitable[None]]


def _sprechbar(s: Any) -> str:
    r"""Text, der VORGELESEN wird — ohne alles, was man nicht sprechen kann.

    Zwei Dinge, die im Sprachkanal nichts verloren haben:

    1. **Zeilenumbrueche.** ``send_tool_result`` verpackt den Text zusaetzlich
       mit ``json.dumps``; ein echter Umbruch wird dabei wieder zu den zwei
       SICHTBAREN Zeichen ``\`` und ``n``. Nova reicht die Zeichenkette
       woertlich ans Modell, das den Backslash nicht sprechen kann — im
       Transkript stand dann „n n1. InsideAI" (gemeldet am 21.08.2026).
       ``_clean_text`` wandelt literale Escapes zwar in echte Umbrueche
       zurueck, aber die naechste Kodierung machte das sofort wieder zunichte.
       Fuer gesprochenen Text traegt ein Umbruch ohnehin keine Bedeutung: ein
       Absatz wird zur Sprechpause, eine Zeile zum Leerzeichen.
    2. **Markdown.** ``**InsideAI**`` wurde als „InsideAI Sternchen Sternchen"
       vorgelesen — im selben Bildschirmfoto zu sehen.

    Bewusst NICHT in ``_clean_text`` eingebaut: das saeubert alles, was in die
    Engine geht (auch Eingespieltes, wo echte Umbrueche unbeschadet ankommen).
    Hier geht es nur um den Weg, der zusaetzlich kodiert wird.
    """
    t = _clean_text(s)
    # Absatz = Sprechpause, einfacher Umbruch = Leerzeichen.
    # Steht davor schon ein Satzzeichen (haeufig ein Doppelpunkt vor einer
    # Aufzaehlung), waere ein zusaetzlicher Punkt zu hoeren: „Ergebnisse:. Eins".
    t = re.sub(r"(?<=[.!?:;])[ \t]*\n[ \t]*\n\s*", " ", t)
    t = re.sub(r"[ \t]*\n[ \t]*\n\s*", ". ", t)
    t = re.sub(r"[ \t]*[\n\r\t][ \t]*", " ", t)
    # Markdown-Auszeichnung: der Inhalt bleibt, die Zeichen fliegen raus.
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)      # [Text](Ziel) -> Text
    t = re.sub(r"(\*\*|__|`+|~~)", "", t)                # fett/kursiv/code
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)          # Ueberschriften
    # Doppelte Satzzeichen aus dem Absatz-Ersatz und Mehrfach-Leerzeichen.
    t = re.sub(r"\.\s*\.(\s|$)", ". ", t)
    t = re.sub(r"[ ]{2,}", " ", t)
    return t.strip()


def _clean_text(s: Any) -> str:
    """Sanitize text going INTO Nova (tool results / injected turns).

    File/PDF-derived tool content can carry NUL bytes, control chars or broken
    UTF-8 (e.g. lone surrogates from pypdf) — sending those over the bidi stream
    makes Bedrock fail the turn with a generic 'unexpected error during
    processing' / decode error. Strip control chars (keep \\n and \\t) and force
    valid UTF-8 so the payload is always clean."""
    if not isinstance(s, str):
        s = str(s)
    # Force valid UTF-8 (drops lone surrogates / undecodable bytes).
    s = s.encode("utf-8", "replace").decode("utf-8", "replace")
    # Literale Escape-Folgen zu echten Zeichen machen. Kommt Text irgendwo als
    # JSON-Zeichenkette an (Werkzeug-Ergebnis, Gedaechtnis-Eintrag, Datei-Auszug),
    # steht dort BACKSLASH+n statt eines Umbruchs. Die Engine kann einen Backslash
    # nicht sprechen — sie laesst ihn weg, und im Transkript steht ueberall ein
    # einsames "n" mitten im Satz ("n1. Backlog n - OAuth"). Ein echter Umbruch ist
    # das, was gemeint war; ein wirklich gemeintes "\n" kommt in gesprochenem Text
    # praktisch nicht vor.
    s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    # Drop NUL + other C0/C1 control chars except tab/newline/carriage-return.
    return "".join(
        ch for ch in s if ch in "\t\n\r" or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )


class NovaSonicSession:
    """One bidirectional Nova Sonic conversation.

    Lifecycle: ``open()`` → stream ``send_audio()`` frames while reading events
    via the ``on_event`` callback → answer ``tool_use`` events with
    ``send_tool_result()`` → ``close()``.
    """

    def __init__(
        self,
        *,
        region: str,
        access_key: str,
        secret_key: str,
        session_token: str | None = None,
        system_prompt: str,
        tools: list[dict] | None = None,
        voice_id: str = "matthew",
        on_event: EventCallback,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        model_id: str = MODEL_ID,
    ) -> None:
        self.region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._session_token = session_token
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.voice_id = voice_id
        self.on_event = on_event
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.model_id = model_id or MODEL_ID

        self._stream: Any = None
        self._client: Any = None
        self._prompt_name = str(uuid.uuid4())
        self._audio_content_name = str(uuid.uuid4())
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._audio_started = False
        # Set once the server has completed the HTTP/2 stream (receive loop ended).
        # Further input writes to a completed stream make awscrt raise
        # AWS_ERROR_HTTP_STREAM_HAS_COMPLETED from an internal task whose exception
        # is never retrieved — so all sends short-circuit once this is True.
        self._input_dead = False
        #: (Art, Bytes) des zuletzt gesendeten Nicht-Audio-Ereignisses — die
        #: Brotkrume fuer „Invalid input request", siehe _send_event.
        self.letztes_ereignis: tuple[str, int] | None = None
        # Serializes multi-event sequences (contentStart→content→contentEnd) so
        # concurrently-fired tool results / text injections can't interleave on the
        # wire. Single-event audio sends stay lock-free (own persistent content block).
        self._seq_lock = asyncio.Lock()

    # ── stream setup ────────────────────────────────────────────────

    async def _config(self):
        # aws-sdk-bedrock-runtime 0.10 renamed Config -> AsyncBedrockRuntimeConfig
        # and BedrockRuntimeClient -> AsyncBedrockRuntimeClient, kept the field
        # names and the auth/models modules intact, but forbids constructing the
        # config directly — it must come from `await ...Config.resolve(...)`.
        # Deployments carry either SDK generation depending on when their image
        # was built, so both paths must work; a missing SDK still fails loudly.
        try:
            from aws_sdk_bedrock_runtime.config import Config

            legacy_sdk = True
        except ImportError:
            from aws_sdk_bedrock_runtime.config import (
                AsyncBedrockRuntimeConfig as Config,
            )

            legacy_sdk = False
        from aws_sdk_bedrock_runtime.auth import HTTPAuthSchemeResolver
        from smithy_aws_core.auth.sigv4 import SigV4AuthScheme
        from smithy_aws_core.identity import AWSCredentialsIdentity
        from smithy_core.aio.interfaces.identity import IdentityResolver

        access, secret, token = self._access_key, self._secret_key, self._session_token

        class _StaticCreds(IdentityResolver):
            async def get_identity(self, *, properties=None):
                return AWSCredentialsIdentity(
                    access_key_id=access,
                    secret_access_key=secret,
                    session_token=token,
                )

        kwargs = {
            "endpoint_uri": f"https://bedrock-runtime.{self.region}.amazonaws.com",
            "region": self.region,
            "aws_credentials_identity_resolver": _StaticCreds(),
            "auth_scheme_resolver": HTTPAuthSchemeResolver(),
            "auth_schemes": {"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},
        }
        if legacy_sdk:
            return Config(**kwargs)
        return await Config.resolve(**kwargs)

    async def _send_event(self, event: dict) -> None:
        # Never write to a stream the server has already completed — awscrt would
        # raise AWS_ERROR_HTTP_STREAM_HAS_COMPLETED from an unretrieved internal task.
        if self._input_dead or self._stream is None:
            return
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamInputChunk as InChunk,
            BidirectionalInputPayloadPart as Payload,
        )
        data = json.dumps({"event": event}).encode("utf-8")
        # Brotkrume fuer den Fehlerfall. Bedrock antwortet auf ein unbrauchbares
        # Ereignis mit „Invalid input request, please fix your input and try
        # again." — ohne zu sagen, WELCHES. Zweimal am 19.08.2026 aufgetreten,
        # und im Log stand nur die Meldung. Hier merken wir uns die Art und
        # Groesse des zuletzt gesendeten Ereignisses (Audio ausgenommen, das
        # waere jede Zehntelsekunde eine Zeile), damit die naechste Meldung
        # sagen kann, worauf sie folgte.
        art = next(iter(event.keys()), "?")
        if art != "audioInput":
            self.letztes_ereignis = (art, len(data))
        await self._stream.input_stream.send(InChunk(value=Payload(bytes_=data)))

    async def open(self) -> None:
        """Open the bidirectional stream and prime it with prompt + system + tools."""
        try:
            from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
        except ImportError:  # SDK >= 0.10, see _config
            from aws_sdk_bedrock_runtime.client import (
                AsyncBedrockRuntimeClient as BedrockRuntimeClient,
            )
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamOperationInput as OpInput,
        )

        self._client = BedrockRuntimeClient(config=await self._config())
        self._stream = await self._client.invoke_model_with_bidirectional_stream(
            OpInput(model_id=self.model_id)
        )

        await self._send_event({"sessionStart": {"inferenceConfiguration": {
            "maxTokens": self.max_tokens, "topP": 0.9, "temperature": self.temperature,
        }}})

        prompt_start: dict = {
            "promptName": self._prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                "sampleSizeBits": 16, "channelCount": 1,
                "voiceId": self.voice_id, "encoding": "base64", "audioType": "SPEECH",
            },
        }
        if self.tools:
            prompt_start["toolUseOutputConfiguration"] = {"mediaType": "application/json"}
            prompt_start["toolConfiguration"] = {"tools": self.tools}
        await self._send_event({"promptStart": prompt_start})

        # System prompt (text content)
        sys_c = str(uuid.uuid4())
        await self._send_event({"contentStart": {
            "promptName": self._prompt_name, "contentName": sys_c, "type": "TEXT",
            "interactive": True, "role": "SYSTEM",
            "textInputConfiguration": {"mediaType": "text/plain"},
        }})
        await self._send_event({"textInput": {
            "promptName": self._prompt_name, "contentName": sys_c, "content": self.system_prompt,
        }})
        await self._send_event({"contentEnd": {"promptName": self._prompt_name, "contentName": sys_c}})

        # Start of the (continuous, interactive) user audio turn
        await self._send_event({"contentStart": {
            "promptName": self._prompt_name, "contentName": self._audio_content_name,
            "type": "AUDIO", "interactive": True, "role": "USER",
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": INPUT_SAMPLE_RATE,
                "sampleSizeBits": 16, "channelCount": 1, "audioType": "SPEECH", "encoding": "base64",
            },
        }})
        self._audio_started = True
        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info("NovaSonicSession opened prompt=%s region=%s", self._prompt_name, self.region)

    # ── inbound audio ───────────────────────────────────────────────

    async def send_audio(self, pcm_16k: bytes) -> None:
        """Stream one chunk of 16 kHz/16-bit/mono PCM to the model."""
        if self._closed or self._input_dead or not self._audio_started:
            return
        b64 = base64.b64encode(pcm_16k).decode("ascii")
        await self._send_event({"audioInput": {
            "promptName": self._prompt_name, "contentName": self._audio_content_name, "content": b64,
        }})

    # ── proactive injection ─────────────────────────────────────────

    async def inject_user_text(self, text: str) -> None:
        """Inject a text turn mid-session to make the model speak proactively.

        Used for the async delegation report: after the agent answers (seconds
        later), we push the result in as a user turn so Nova Sonic voices it
        without the user having to ask again.
        """
        if self._closed:
            return
        content_name = str(uuid.uuid4())
        async with self._seq_lock:
            await self._send_event({"contentStart": {
                "promptName": self._prompt_name, "contentName": content_name, "type": "TEXT",
                "interactive": True, "role": "USER",
                "textInputConfiguration": {"mediaType": "text/plain"},
            }})
            # `_sprechbar` statt `_clean_text`: dieser Text wird genau wie ein
            # Tool-Ergebnis vorgelesen (siehe send_tool_result) — dieselbe
            # zusaetzliche Kodierung, derselbe "n1./n2."-Bug (erneut gemeldet
            # am 26.08.2026), nur an dieser zweiten Stelle war der Fix vom
            # 21.08. noch nicht nachgezogen.
            await self._send_event({"textInput": {
                "promptName": self._prompt_name, "contentName": content_name, "content": _sprechbar(text),
            }})
            await self._send_event({"contentEnd": {
                "promptName": self._prompt_name, "contentName": content_name,
            }})

    # ── tool result ─────────────────────────────────────────────────

    async def send_tool_result(self, tool_use_id: str, result: str) -> None:
        """Answer a toolUse: feed the agent's response back so the model speaks it."""
        if self._closed:
            return
        content_name = str(uuid.uuid4())
        async with self._seq_lock:
            await self._send_event({"contentStart": {
                "promptName": self._prompt_name, "contentName": content_name,
                "interactive": False, "type": "TOOL", "role": "TOOL",
                "toolResultInputConfiguration": {
                    "toolUseId": tool_use_id, "type": "TEXT",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                },
            }})
            # Nova Sonic requires the tool result content as a JSON string, not prose.
            await self._send_event({"toolResult": {
                "promptName": self._prompt_name, "contentName": content_name,
                # `_sprechbar` statt `_clean_text`: der Text geht hier durch eine
                # ZWEITE Kodierung, die echte Umbrueche wieder sichtbar machen
                # wuerde. Siehe dort.
                "content": json.dumps({"result": _sprechbar(result)}),
            }})
            await self._send_event({"contentEnd": {
                "promptName": self._prompt_name, "contentName": content_name,
            }})

    # ── receive loop ────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        try:
            while not self._closed:
                out = await self._stream.await_output()
                recv = out[1] if isinstance(out, (tuple, list)) else out
                result = await recv.receive()
                if result is None:
                    break
                val = getattr(getattr(result, "value", None), "bytes_", None)
                if val is None:
                    continue
                # Tolerant decode: a single malformed byte from the service must not
                # kill the loop with an "invalid byte" UnicodeDecodeError — best-effort
                # parse and skip anything that still isn't valid JSON.
                try:
                    data = json.loads(val.decode("utf-8", "replace"))
                except (ValueError, TypeError):
                    logger.debug("NovaSonic: skipped an undecodable stream frame")
                    continue
                event = data.get("event", {})
                if not event:
                    continue
                await self._dispatch(next(iter(event.keys())), event)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # Always log (even if already closing) so the real Bedrock error is never
            # invisible — this is how "unexpected error during processing" surfaces.
            logger.warning(
                "NovaSonic receive loop error (closed=%s, zuletzt gesendet=%s): %r",
                self._closed, self.letztes_ereignis, e, exc_info=True,
            )
            if not self._closed:
                await self._safe_emit("error", {"message": str(e)})
        finally:
            # The server has completed the stream; any further input write would
            # crash awscrt with an unretrieved-task exception. Fence off all sends.
            self._input_dead = True
            await self._safe_emit("done", {})

    async def _dispatch(self, kind: str, event: dict) -> None:
        payload = event.get(kind, {}) or {}
        if kind == "audioOutput":
            pcm = base64.b64decode(payload.get("content", ""))
            await self._safe_emit("audio", {"pcm": pcm})
        elif kind == "textOutput":
            content = payload.get("content", "")
            # Nova sometimes emits a JSON metadata blob (e.g. {"interrupted": true})
            # as textOutput content — that's a signal, not spoken text. Never show it.
            s = content.strip() if isinstance(content, str) else ""
            if s.startswith("{") and s.endswith("}"):
                try:
                    meta = json.loads(s)
                except Exception:  # noqa: BLE001
                    meta = None
                if isinstance(meta, dict):
                    if meta.get("interrupted"):
                        await self._safe_emit("interrupted", {})
                    return
            await self._safe_emit("text", {"text": content, "role": payload.get("role", "")})
        elif kind == "toolUse":
            await self._safe_emit("tool_use", {
                "tool_use_id": payload.get("toolUseId", ""),
                "name": payload.get("toolName", ""),
                "input": payload.get("content", ""),
            })
        elif kind == "usageEvent":
            await self._safe_emit("usage", payload)
        elif kind == "contentStart":
            # Start of a new content block = a new turn segment. The session uses
            # this as the authoritative "the interrupted audio is over now" signal.
            # Nova 2 marks text blocks with a generationStage (SPECULATIVE/FINAL) in
            # additionalModelFields (a JSON *string*). Log it so we can tell the model's
            # thinking blocks apart from the spoken answer — see the reasoning-leak issue.
            if payload.get("type") == "TEXT":
                amf = payload.get("additionalModelFields")
                if amf:
                    logger.info("NovaSonic contentStart TEXT role=%s additionalModelFields=%s",
                                payload.get("role", ""), amf)
            await self._safe_emit("content_start", {
                "role": payload.get("role", ""),
                "type": payload.get("type", ""),
            })
        elif kind in ("contentEnd", "completionStart", "completionEnd"):
            pass  # lifecycle only
        elif kind in ("userSpeechStart", "userSpeechEnd"):
            # Nova's server-side VAD telemetry (user began/stopped speaking).
            # Barge-in audio interruption is already driven by the `interrupted`
            # textOutput signal above, so these are informational only — log at
            # debug, never WARNING, to avoid flooding the error log on every
            # utterance (they arrive twice per user turn).
            logger.debug("NovaSonic VAD event kind=%s", kind)
        else:
            # Bedrock modeled exceptions (internalServerException / modelStreamError /
            # validationException / throttlingException) arrive as their own event
            # kind — surface them so "unexpected error during processing" is diagnosable.
            msg = payload.get("message") if isinstance(payload, dict) else ""
            logger.warning("NovaSonic UNHANDLED event kind=%s message=%s payload=%s",
                           kind, msg, str(payload)[:400])
            if "exception" in kind.lower() or "error" in kind.lower():
                await self._safe_emit("error", {"message": msg or f"Nova: {kind}"})

    async def _safe_emit(self, kind: str, data: dict) -> None:
        try:
            await self.on_event(kind, data)
        except Exception:  # noqa: BLE001
            logger.warning("NovaSonic on_event(%s) handler failed", kind, exc_info=True)

    # ── teardown ────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._audio_started:
                await self._send_event({"contentEnd": {
                    "promptName": self._prompt_name, "contentName": self._audio_content_name,
                }})
            await self._send_event({"promptEnd": {"promptName": self._prompt_name}})
            await self._send_event({"sessionEnd": {}})
            await self._stream.input_stream.close()
        except Exception:  # noqa: BLE001
            logger.debug("NovaSonic close cleanup error", exc_info=True)
        finally:
            # Input stream is closed now — block any concurrent send from racing a
            # write onto the completed stream during teardown.
            self._input_dead = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass


def credentials_from_env() -> dict | None:
    """Read AWS creds for Nova Sonic from the environment (Pi-only wiring).

    Returns None if not configured, so the caller can fall back cleanly.
    """
    access = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not access or not secret:
        return None
    return {
        "access_key": access,
        "secret_key": secret,
        "session_token": os.environ.get("AWS_SESSION_TOKEN") or None,
        "region": os.environ.get("NOVA_SONIC_REGION")
        or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    }
