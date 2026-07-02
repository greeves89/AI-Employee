"""Regression tests for temperature handling in the OpenAI-compatible provider.

Some models only accept the default temperature (1) and return HTTP 400 on any
custom value (e.g. Azure ``gpt-chat-latest``, the o-series reasoning models, and
the GPT-5/codex Responses-API models). The provider must omit ``temperature``
for those while still sending it for ordinary chat models.
"""

import pytest

from app.providers.openai_provider import OpenAIProvider


def _provider(model: str, temperature: float = 0.7) -> OpenAIProvider:
    return OpenAIProvider(
        api_endpoint="https://example.invalid",
        api_key="test",
        model_name=model,
        temperature=temperature,
    )


@pytest.mark.parametrize(
    "model",
    ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "llama-3.1-70b", "qwen2.5-72b", "mistral-large"],
)
def test_ordinary_models_support_custom_temperature(model):
    assert _provider(model)._supports_custom_temperature() is True


@pytest.mark.parametrize(
    "model",
    [
        "gpt-chat-latest",
        "gpt-4o-chat-latest",
        "o1",
        "o1-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "gpt-5",
        "gpt-5.4",
        "codex-mini",
    ],
)
def test_temperature_locked_models_omit_temperature(model):
    assert _provider(model)._supports_custom_temperature() is False


def test_chat_body_omits_temperature_for_gpt_chat_latest():
    body = _provider("gpt-chat-latest")._build_chat_body([], None)
    assert "temperature" not in body
    # chat-completions path still uses max_tokens (not a Responses model)
    assert body["max_tokens"] == _provider("gpt-chat-latest").max_tokens


def test_chat_body_includes_temperature_for_ordinary_model():
    body = _provider("gpt-4o", temperature=0.3)._build_chat_body([], None)
    assert body["temperature"] == 0.3
    assert "max_tokens" in body


def test_responses_model_uses_max_completion_tokens_and_no_temperature():
    body = _provider("gpt-5.4")._build_chat_body([], None)
    assert "temperature" not in body
    assert "max_tokens" not in body
    assert "max_completion_tokens" in body
