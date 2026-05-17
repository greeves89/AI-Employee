"""Central model registry — context window sizes and token pricing.

Single source of truth for every LLM the custom-LLM runtime may run on.
Previously these tables were duplicated (and drifting) across llm_runner.py
and llm_chat_handler.py. Adding a new model now means editing one place.

Each entry: (context_window_tokens, input_price_per_1m_usd, output_price_per_1m_usd).
A price of None means "unknown" — cost estimation returns 0 for that model.
"""

from __future__ import annotations

# model-id substring → (context_window, input_$/1M, output_$/1M)
# Matching is done by substring (longest match wins) so dated variants like
# "gpt-4o-2024-08-06" resolve to the "gpt-4o" entry.
_MODELS: dict[str, tuple[int, float | None, float | None]] = {
    # OpenAI
    "gpt-4o-mini": (128_000, 0.15, 0.60),
    "gpt-4o": (128_000, 2.50, 10.00),
    "gpt-4-turbo": (128_000, 10.00, 30.00),
    "gpt-4": (8_192, 30.00, 60.00),
    "gpt-3.5-turbo": (16_385, 0.50, 1.50),
    "gpt-5": (1_000_000, 1.25, 10.00),
    "o1-mini": (128_000, 3.00, 12.00),
    "o1": (200_000, 15.00, 60.00),
    "o3-mini": (200_000, 1.10, 4.40),
    # Anthropic
    "claude-opus-4-7": (1_000_000, 15.00, 75.00),
    "claude-opus-4-6": (200_000, 15.00, 75.00),
    "claude-opus-4": (200_000, 15.00, 75.00),
    "claude-sonnet-4-6": (200_000, 3.00, 15.00),
    "claude-sonnet-4": (200_000, 3.00, 15.00),
    "claude-haiku-4-5": (200_000, 1.00, 5.00),
    "claude-haiku-4": (200_000, 1.00, 5.00),
    # Google
    "gemini-2.5-pro": (1_000_000, 1.25, 10.00),
    "gemini-2.5-flash": (1_000_000, 0.30, 2.50),
    "gemini-1.5-pro": (2_000_000, 1.25, 5.00),
    "gemini-1.5-flash": (1_000_000, 0.075, 0.30),
    # Local / open models — conservative defaults, no billing
    "llama": (8_192, None, None),
    "mistral": (32_768, None, None),
    "codestral": (32_768, None, None),
    "deepseek": (128_000, None, None),
    "qwen": (128_000, None, None),
}

DEFAULT_CONTEXT_WINDOW = 128_000


def _lookup(model: str) -> tuple[int, float | None, float | None] | None:
    """Resolve a model id to its registry entry via longest-substring match."""
    if not model:
        return None
    m = model.lower()
    best_key = ""
    for key in _MODELS:
        if key in m and len(key) > len(best_key):
            best_key = key
    return _MODELS[best_key] if best_key else None


def get_context_window(model: str) -> int:
    """Context window (tokens) for a model, or DEFAULT_CONTEXT_WINDOW if unknown."""
    entry = _lookup(model)
    return entry[0] if entry else DEFAULT_CONTEXT_WINDOW


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a completion. Returns 0.0 if pricing is unknown."""
    entry = _lookup(model)
    if not entry:
        return 0.0
    _, in_price, out_price = entry
    if in_price is None or out_price is None:
        return 0.0
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


def is_known(model: str) -> bool:
    """Whether the model is present in the registry."""
    return _lookup(model) is not None
