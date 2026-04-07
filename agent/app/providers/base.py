"""Base LLM provider interface."""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)


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
        """Clean up resources (HTTP clients, etc)."""
        pass
