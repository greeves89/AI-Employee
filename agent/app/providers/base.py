"""Base LLM provider interface."""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


def _cause_chain(exc: BaseException, tiefe: int = 4) -> list[str]:
    """Die Kette darunterliegender Fehler — dort steht der eigentliche Grund.

    ``httpx`` verpackt den Socket-Fehler: ein abgerissener Strom kommt oben als
    ``ReadError('')`` an, waehrend darunter ``ConnectionResetError``,
    ``SSLEOFError``, ``EndOfStream`` oder ``IncompleteRead`` steht. Ohne diese
    Kette ist die Meldung buchstaeblich leer — genau der Fall, der am 2026-08-13
    beim Kunden dreimal auftrat und nur durch Archaeologie in den Aufgaben-
    schritten einzugrenzen war.
    """
    glieder: list[str] = []
    gesehen: set[int] = {id(exc)}
    aktuell: BaseException | None = exc
    while len(glieder) < tiefe:
        aktuell = aktuell.__cause__ or aktuell.__context__
        if aktuell is None or id(aktuell) in gesehen:
            break
        gesehen.add(id(aktuell))
        text = str(aktuell).strip()
        glieder.append(f"{type(aktuell).__name__}: {text}" if text else type(aktuell).__name__)
    return glieder


def format_exception(exc: BaseException) -> str:
    """Human-readable exception string that is NEVER empty.

    Some exceptions (timeouts, certain OpenAI/httpx SDK errors) have an empty str(),
    which produced the useless task error 'Unexpected error: '. Fall back to the type
    name / repr so failures are always debuggable.

    Reicht der eigene Text nicht (``ReadError('')``), wird die Ursachenkette
    angehaengt — sonst wirft man genau die Information weg, wegen der man den
    Fehler ueberhaupt liest.
    """
    msg = str(exc).strip()
    kopf = f"{type(exc).__name__}: {msg}" if msg else (repr(exc) or type(exc).__name__)
    kette = _cause_chain(exc)
    return f"{kopf} <- {' <- '.join(kette)}" if kette else kopf


def describe_failure(
    exc: BaseException,
    *,
    url: str = "",
    body: dict | None = None,
    messages: list | None = None,
    model: str = "",
    started: float | None = None,
) -> str:
    """Der Fehler PLUS die Umstaende, unter denen er auftrat.

    Ohne diese Umstaende ist ein abgerissener Strom nicht diagnostizierbar: man
    sieht „ReadError('')" und weiss weder, wie gross die Anfrage war, noch an
    welchem Endpunkt sie hing, noch ob sie nach einer Sekunde oder nach zwei
    Minuten abriss. Beim Kunden kostete genau das eine Stunde Rekonstruktion aus
    den Aufgabenschritten — fuer eine Information, die hier in einer Zeile
    haette stehen koennen.

    Bewusst OHNE Inhalte: nur Groessen, Anzahl und Host. Der Prompt gehoert nicht
    in eine Fehlermeldung, die in der Oberflaeche und in Protokollen landet.
    """
    teile: list[str] = [format_exception(exc)]
    umstaende: list[str] = []
    if model:
        umstaende.append(f"Modell={model}")
    if url:
        # Nur Host und letzter Pfadteil — Abfragezeichenfolgen koennen Schluessel tragen.
        ohne_frage = url.split("?", 1)[0]
        teil = ohne_frage.split("://", 1)[-1]
        host = teil.split("/", 1)[0]
        umstaende.append(f"Endpunkt={host}/…/{ohne_frage.rsplit('/', 1)[-1]}")
    if messages is not None:
        umstaende.append(f"Nachrichten={len(messages)}")
    if body is not None:
        try:
            import json as _json

            umstaende.append(f"Anfrage={len(_json.dumps(body)):,} Zeichen")
        except Exception:  # noqa: BLE001 — eine Diagnose darf nie selbst scheitern
            pass
    if started is not None:
        umstaende.append(f"nach {time.monotonic() - started:.1f}s")
    if umstaende:
        teile.append(f"[{', '.join(umstaende)}]")
    return " ".join(teile)


@dataclass
class LLMEvent:
    """Normalized event emitted by all providers."""
    type: str  # "text_delta" | "tool_call" | "tool_call_done" | "done" | "error"
    text: str = ""
    tool_name: str = ""
    tool_id: str = ""
    tool_input: dict = field(default_factory=dict)
    # Usage stats (only on "done" events)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChatMessage:
    """A single message in the conversation."""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list | None = ""
    tool_call_id: str = ""
    name: str = ""
    tool_calls: list = field(default_factory=list)  # For assistant messages with tool calls


class CircuitBreaker:
    """Simple circuit breaker for external API calls.

    States:
      CLOSED  — normal operation, requests go through
      OPEN    — too many failures, requests fail immediately
      HALF    — after cooldown, allow one probe request

    When OPEN, callers get an immediate error instead of waiting
    for a 300s timeout on a dead API.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
        name: str = "api",
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.name = name
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0

    @property
    def state(self) -> str:
        if self._state == self.OPEN:
            # Check if cooldown has passed → transition to HALF_OPEN
            if time.time() - self._last_failure_time >= self.cooldown_seconds:
                self._state = self.HALF_OPEN
                logger.info(f"[CircuitBreaker:{self.name}] HALF_OPEN — allowing probe request")
        return self._state

    def check(self) -> None:
        """Call before making a request. Raises RuntimeError if circuit is OPEN."""
        if self.state == self.OPEN:
            wait = int(self.cooldown_seconds - (time.time() - self._last_failure_time))
            raise RuntimeError(
                f"Circuit breaker OPEN for {self.name}: API unreachable after "
                f"{self.failure_threshold} consecutive failures. "
                f"Retrying in {max(wait, 1)}s."
            )

    def record_success(self) -> None:
        """Call after a successful request."""
        if self._state != self.CLOSED:
            logger.info(f"[CircuitBreaker:{self.name}] CLOSED — API recovered")
        self._failure_count = 0
        self._state = self.CLOSED

    def record_failure(self) -> None:
        """Call after a failed request (timeout, 5xx, connection error)."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = self.OPEN
            logger.warning(
                f"[CircuitBreaker:{self.name}] OPEN — "
                f"{self._failure_count} consecutive failures, "
                f"blocking requests for {self.cooldown_seconds}s"
            )


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(
        self,
        api_endpoint: str,
        api_key: str,
        model_name: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs,
    ):
        self.api_endpoint = api_endpoint.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.circuit_breaker = CircuitBreaker(name=model_name or "llm")
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Lazy, self-healing HTTP client.

        Recreated whenever it is missing or closed. When the user sends a
        new message mid-response, the handler calls close() to interrupt the
        in-flight request — without this, the next request would fail
        permanently with "client has been closed". Here it just heals.
        """
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        return self._http

    @abstractmethod
    async def _stream_completion_impl(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Internal implementation — subclasses override this."""
        ...

    async def stream_completion(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream a completion with circuit breaker protection.

        If the API has failed too many times consecutively, this will
        immediately yield an error instead of waiting for another timeout.
        """
        try:
            self.circuit_breaker.check()
        except RuntimeError as e:
            yield LLMEvent(type="error", text=str(e))
            return

        had_error = False
        async for event in self._stream_completion_impl(messages, tools):
            if event.type == "error":
                had_error = True
                self.circuit_breaker.record_failure()
            elif event.type == "done" and not had_error:
                self.circuit_breaker.record_success()
            yield event

    async def close(self) -> None:
        """Close the HTTP client. Safe to call repeatedly — the `http`
        property recreates it on next use."""
        if self._http is not None and not self._http.is_closed:
            try:
                await self._http.aclose()
            except Exception:
                pass
        self._http = None
