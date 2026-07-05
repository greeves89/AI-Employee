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


def test_anthropic_tools_deduped_and_nonempty():
    """Anthropic rejects duplicate/empty tool names (400). The converter must
    drop collisions (first wins) and empty names, and set exactly one cache
    breakpoint on the last tool."""
    from app.providers.anthropic_provider import _to_anthropic_tools
    tools = [
        {"function": {"name": "a", "description": "1", "parameters": {}}},
        {"function": {"name": "b", "description": "2", "parameters": {}}},
        {"function": {"name": "a", "description": "dup", "parameters": {}}},  # dup
        {"function": {"name": "", "description": "empty", "parameters": {}}},  # empty
    ]
    out = _to_anthropic_tools(tools)
    names = [t["name"] for t in out]
    assert names == ["a", "b"], names
    assert out[0]["description"] == "1"  # first occurrence wins
    assert out[-1].get("cache_control") == {"type": "ephemeral"}
    assert sum("cache_control" in t for t in out) == 1


def test_anthropic_tools_empty_input():
    from app.providers.anthropic_provider import _to_anthropic_tools
    assert _to_anthropic_tools([]) == []
    assert _to_anthropic_tools(None) == []


def test_azure_v1_surface_routes_codex_to_responses():
    """Azure's /openai/v1 surface: codex/GPT-5 reasoning models are responses-only
    (400 on chat/completions). Must route to /responses; plain chat models to
    /chat/completions — NOT the classic per-deployment path."""
    from app.providers.openai_provider import OpenAIProvider
    ep = "https://x.services.ai.azure.com/openai/v1"
    codex = OpenAIProvider(api_endpoint=ep, api_key="K", model_name="gpt-5.3-codex", is_azure=True)
    assert codex._resolve_url() == (f"{ep}/responses", "responses")
    chat = OpenAIProvider(api_endpoint=ep, api_key="K", model_name="gpt-chat-latest", is_azure=True)
    assert chat._resolve_url() == (f"{ep}/chat/completions", "chat")
