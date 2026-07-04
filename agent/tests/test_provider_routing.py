"""Regression tests for LLM provider routing (create_provider factory).

Covers the Azure "Claude/Anthropic surface" case: an Azure OpenAI resource that
exposes Claude via …/anthropic/v1/messages speaks the Anthropic Messages API
(x-api-key + anthropic-version), not the OpenAI protocol. It must route to the
AnthropicProvider even though it is configured with provider_type "azure-openai"
(keeping that type keeps the agent on the custom_llm harness rather than the
claude_code CLI). See providers/__init__.py.
"""

from app.providers import create_provider
from app.providers.openai_provider import OpenAIProvider
from app.providers.anthropic_provider import AnthropicProvider


def test_azure_anthropic_surface_routes_to_anthropic_provider():
    p = create_provider(
        provider_type="azure-openai",
        api_endpoint="https://x.services.ai.azure.com/anthropic/v1/messages",
        api_key="K",
        model_name="claude-opus-4-8",
    )
    assert isinstance(p, AnthropicProvider)
    # AnthropicProvider appends "/messages" itself → base must not carry it.
    assert p.api_endpoint == "https://x.services.ai.azure.com/anthropic/v1"
    assert f"{p.api_endpoint}/messages" == (
        "https://x.services.ai.azure.com/anthropic/v1/messages"
    )


def test_azure_anthropic_surface_accepts_base_without_messages():
    p = create_provider(
        provider_type="azure-openai",
        api_endpoint="https://x.services.ai.azure.com/anthropic/v1",
        api_key="K",
        model_name="claude-opus-4-8",
    )
    assert isinstance(p, AnthropicProvider)
    assert p.api_endpoint == "https://x.services.ai.azure.com/anthropic/v1"


def test_plain_azure_openai_stays_openai_provider():
    p = create_provider(
        provider_type="azure-openai",
        api_endpoint="https://x.openai.azure.com",
        api_key="K",
        model_name="gpt-4.1",
    )
    assert isinstance(p, OpenAIProvider)


def test_native_anthropic_stays_anthropic_provider():
    p = create_provider(
        provider_type="anthropic",
        api_endpoint="https://api.anthropic.com/v1",
        api_key="K",
        model_name="claude",
    )
    assert isinstance(p, AnthropicProvider)
