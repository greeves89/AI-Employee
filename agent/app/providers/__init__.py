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
    ep = (api_endpoint or "").rstrip("/")
    # Azure's Claude/Anthropic surface (…/anthropic/v1/messages) speaks the
    # Anthropic Messages API (x-api-key + anthropic-version), NOT the OpenAI
    # protocol — even though it lives on an Azure OpenAI resource and is thus
    # naturally configured with provider_type "azure-openai". Detect it by the
    # "/anthropic/" path and route to the AnthropicProvider. Keeping the type
    # as azure-openai is important: the orchestrator's harness mapping only
    # flips to the claude_code CLI for type "anthropic", so this stays on the
    # custom_llm harness (which is what talks to this surface).
    if "/anthropic/" in ep and provider_type in ("openai", "azure-openai", "azure"):
        from app.providers.anthropic_provider import AnthropicProvider
        # AnthropicProvider appends "/messages"; strip it if the configured
        # endpoint already carries the full path (both forms are accepted).
        base = ep[: -len("/messages")] if ep.endswith("/messages") else ep
        return AnthropicProvider(
            api_endpoint=base,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
    # Azure OpenAI speaks the OpenAI-compatible API — same provider class;
    # the Azure resource URL is the api_endpoint. is_azure drives the
    # classic deployment URL build (/openai/deployments/{model}/...).
    if provider_type in ("openai", "azure-openai", "azure", ""):
        return OpenAIProvider(
            api_endpoint=api_endpoint,
            api_key=api_key,
            model_name=model_name,
            is_azure=provider_type in ("azure-openai", "azure"),
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
    elif provider_type in ("ollama", "lm-studio", "lmstudio"):
        # Ollama and LM Studio expose an OpenAI-compatible API — no API key required
        return OpenAIProvider(
            api_endpoint=api_endpoint,
            api_key=api_key or "not-required",
            model_name=model_name,
            **kwargs,
        )
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


__all__ = ["BaseLLMProvider", "LLMEvent", "create_provider"]
