"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


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

    @abstractmethod
    async def stream_completion(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream a completion from the LLM.

        Yields LLMEvent objects as the response streams in.
        The final event should be of type "done".
        """
        ...

    async def close(self) -> None:
        """Clean up resources (HTTP clients, etc)."""
        pass
