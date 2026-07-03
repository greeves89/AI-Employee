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

    # ── stream setup ────────────────────────────────────────────────

    def _config(self):
        from aws_sdk_bedrock_runtime.config import Config
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

        return Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=_StaticCreds(),
            auth_scheme_resolver=HTTPAuthSchemeResolver(),
            auth_schemes={"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},
        )

    async def _send_event(self, event: dict) -> None:
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamInputChunk as InChunk,
            BidirectionalInputPayloadPart as Payload,
        )
        data = json.dumps({"event": event}).encode("utf-8")
        await self._stream.input_stream.send(InChunk(value=Payload(bytes_=data)))

    async def open(self) -> None:
        """Open the bidirectional stream and prime it with prompt + system + tools."""
        from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamOperationInput as OpInput,
        )

        self._client = BedrockRuntimeClient(config=self._config())
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
        if self._closed or not self._audio_started:
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
        await self._send_event({"contentStart": {
            "promptName": self._prompt_name, "contentName": content_name, "type": "TEXT",
            "interactive": True, "role": "USER",
            "textInputConfiguration": {"mediaType": "text/plain"},
        }})
        await self._send_event({"textInput": {
            "promptName": self._prompt_name, "contentName": content_name, "content": text,
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
            "content": json.dumps({"result": result}),
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
                data = json.loads(val.decode("utf-8"))
                event = data.get("event", {})
                if not event:
                    continue
                await self._dispatch(next(iter(event.keys())), event)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if not self._closed:
                logger.warning("NovaSonic receive loop error: %s", e, exc_info=True)
                await self._safe_emit("error", {"message": str(e)})
        finally:
            await self._safe_emit("done", {})

    async def _dispatch(self, kind: str, event: dict) -> None:
        payload = event.get(kind, {}) or {}
        if kind == "audioOutput":
            pcm = base64.b64decode(payload.get("content", ""))
            await self._safe_emit("audio", {"pcm": pcm})
        elif kind == "textOutput":
            await self._safe_emit("text", {
                "text": payload.get("content", ""),
                "role": payload.get("role", ""),
            })
        elif kind == "toolUse":
            await self._safe_emit("tool_use", {
                "tool_use_id": payload.get("toolUseId", ""),
                "name": payload.get("toolName", ""),
                "input": payload.get("content", ""),
            })
        elif kind == "usageEvent":
            await self._safe_emit("usage", payload)
        # contentStart/contentEnd/completionStart/completionEnd: lifecycle only

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
