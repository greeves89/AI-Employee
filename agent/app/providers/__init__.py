"""LLM Provider abstraction layer."""

from app.providers.base import BaseLLMProvider, LLMEvent
from app.providers.openai_provider import OpenAIProvider


def create_provider(
    provider_type: str,
    api_endpoint: str,
    api_key: str,
    model_name: str,
    **kwargs,
) -> BaseLLMProvider:
    """Factory: create the right provider instance based on type."""
    if provider_type in ("openai", ""):
        return OpenAIProvider(
            api_endpoint=api_endpoint,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
    elif provider_type == "anthropic":
        # Anthropic provider uses OpenAI-compatible Messages API format
        # but with different endpoint structure — placeholder for Phase 5
        from app.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(
            api_endpoint=api_endpoint,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
    elif provider_type == "google":
        from app.providers.google_provider import GoogleProvider
        return GoogleProvider(
            api_endpoint=api_endpoint,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


__all__ = ["BaseLLMProvider", "LLMEvent", "create_provider"]
