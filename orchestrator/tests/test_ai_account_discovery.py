import pytest

from app.services.ai_account_discovery import (
    AI_ACCOUNT_AUTH_FAILED,
    AI_ACCOUNT_OK,
    AI_ACCOUNT_PROTOCOL_ERROR,
    AI_ACCOUNT_UNREACHABLE,
    AI_ACCOUNT_UNSUPPORTED,
    build_discovery_request,
    classify_http_status,
    discover_models,
    parse_models,
)


def test_openai_request_appends_v1_models():
    req = build_discovery_request("openai", "https://api.openai.com", "sk-x")
    assert req["url"] == "https://api.openai.com/v1/models"
    assert req["headers"]["Authorization"] == "Bearer sk-x"


def test_openai_endpoint_already_ending_in_v1_only_gets_models():
    req = build_discovery_request("ollama", "http://litellm.local/v1", "k")
    assert req["url"] == "http://litellm.local/v1/models"


def test_openai_default_endpoint_used_when_blank():
    req = build_discovery_request("openai", None, "sk")
    assert req["url"] == "https://api.openai.com/v1/models"


def test_selfhosted_without_endpoint_is_unsupported():
    # ollama/lm-studio have no default host, so a blank endpoint yields no request.
    assert build_discovery_request("ollama", "", "k") is None


def test_anthropic_uses_x_api_key_header():
    req = build_discovery_request("anthropic", None, "sk-ant")
    assert req["url"] == "https://api.anthropic.com/v1/models"
    assert req["headers"]["x-api-key"] == "sk-ant"
    assert req["headers"]["anthropic-version"]


def test_google_uses_key_query_param():
    req = build_discovery_request("google", None, "AIza")
    assert req["url"].endswith("/v1beta/models")
    assert req["params"] == {"key": "AIza"}


def test_bedrock_has_no_discovery():
    assert build_discovery_request("bedrock", "https://x", "k") is None


def test_parse_openai_shape_dedupes_and_skips_empty():
    payload = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o"}, {"id": ""}, {"nope": 1}]}
    assert parse_models(payload, "openai") == [{"id": "gpt-4o", "label": "gpt-4o"}]


def test_parse_google_strips_models_prefix_and_prefers_display_name():
    payload = {"models": [{"name": "models/gemini-1.5-pro", "displayName": "Gemini 1.5 Pro"}]}
    assert parse_models(payload, "google") == [
        {"id": "gemini-1.5-pro", "label": "Gemini 1.5 Pro"}
    ]


def test_classify_http_status():
    assert classify_http_status(200) == AI_ACCOUNT_OK
    assert classify_http_status(401) == AI_ACCOUNT_AUTH_FAILED
    assert classify_http_status(403) == AI_ACCOUNT_AUTH_FAILED
    assert classify_http_status(500) == AI_ACCOUNT_PROTOCOL_ERROR


@pytest.mark.asyncio
async def test_discover_ok_returns_models():
    async def fetch(url, headers, params):
        return 200, {"data": [{"id": "gpt-4o"}]}

    result = await discover_models("openai", "https://api.openai.com", "sk", fetch)
    assert result["status"] == AI_ACCOUNT_OK
    assert result["models"] == [{"id": "gpt-4o", "label": "gpt-4o"}]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_discover_auth_failed_on_401():
    async def fetch(url, headers, params):
        return 401, {"error": {"message": "no key"}}

    result = await discover_models("openai", "https://api.openai.com", "", fetch)
    assert result["status"] == AI_ACCOUNT_AUTH_FAILED
    assert result["models"] == []


@pytest.mark.asyncio
async def test_discover_unreachable_when_fetch_signals_none():
    async def fetch(url, headers, params):
        return None, None

    result = await discover_models("openai", "https://api.openai.com", "sk", fetch)
    assert result["status"] == AI_ACCOUNT_UNREACHABLE


@pytest.mark.asyncio
async def test_discover_unreachable_when_fetch_raises():
    async def fetch(url, headers, params):
        raise RuntimeError("connection reset")

    result = await discover_models("openai", "https://api.openai.com", "sk", fetch)
    assert result["status"] == AI_ACCOUNT_UNREACHABLE


@pytest.mark.asyncio
async def test_discover_unsupported_provider_short_circuits():
    calls = []

    async def fetch(url, headers, params):
        calls.append(url)
        return 200, {}

    result = await discover_models("bedrock", "https://x", "k", fetch)
    assert result["status"] == AI_ACCOUNT_UNSUPPORTED
    assert result["models"] == []
    assert calls == []  # no network attempt for an unsupported provider


@pytest.mark.asyncio
async def test_discover_protocol_error_on_500():
    async def fetch(url, headers, params):
        return 500, None

    result = await discover_models("openai", "https://api.openai.com", "sk", fetch)
    assert result["status"] == AI_ACCOUNT_PROTOCOL_ERROR
