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


# ---- Foundry + Azure-Deployments (Kundenbefund 2026-08-18) -----------------
# "foundry" hatte schlicht keinen Suchpfad — deployte Modelle tauchten nie auf.

from app.services.ai_account_discovery import build_discovery_candidates  # noqa: E402


def test_foundry_probes_anthropic_openai_and_catalog_paths():
    cands = build_discovery_candidates("foundry", "https://res.services.ai.azure.com", "k1")
    urls = [c["url"] for c in cands]
    assert urls == [
        "https://res.services.ai.azure.com/anthropic/v1/models",
        "https://res.services.ai.azure.com/openai/v1/models",
        "https://res.services.ai.azure.com/models",
    ]
    # beide Auth-Stile, weil Foundry je nach Flaeche anders prueft
    assert cands[0]["headers"]["x-api-key"] == "k1"
    assert cands[0]["headers"]["api-key"] == "k1"


def test_foundry_resource_name_becomes_full_url():
    """Operatoren tragen (wie in den Provider-Einstellungen) oft nur den
    Ressourcennamen ein."""
    cands = build_discovery_candidates("foundry", "meine-ressource", "k")
    assert cands[0]["url"].startswith("https://meine-ressource.services.ai.azure.com/")


def test_foundry_without_endpoint_is_unsupported():
    assert build_discovery_candidates("foundry", "", "k") == []


def test_azure_openai_falls_back_to_deployment_catalog():
    cands = build_discovery_candidates("azure-openai", "https://res.openai.azure.com", "k")
    urls = [c["url"] for c in cands]
    assert urls[0].endswith("/v1/models")
    assert "https://res.openai.azure.com/openai/v1/models" in urls
    assert any(u.endswith("/openai/deployments") for u in urls)
    dep = [c for c in cands if c["url"].endswith("/openai/deployments")][0]
    assert dep["headers"]["api-key"] == "k"
    assert dep["params"]["api-version"]


@pytest.mark.asyncio
async def test_discover_takes_the_first_candidate_that_answers_200():
    calls = []

    async def fetch(url, headers, params):
        calls.append(url)
        if url.endswith("/openai/v1/models"):
            return 200, {"data": [{"id": "gpt-5.6-sol"}]}
        return 404, {}

    out = await discover_models("foundry", "https://res.services.ai.azure.com", "k", fetch)
    assert out["status"] == AI_ACCOUNT_OK
    assert out["models"] == [{"id": "gpt-5.6-sol", "label": "gpt-5.6-sol"}]
    # der Anthropic-Pfad wurde davor probiert, der Katalog danach nicht mehr
    assert calls[0].endswith("/anthropic/v1/models")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_discover_reports_auth_failure_over_missing_paths():
    """404 auf einem Pfad, den es bei dieser Deployment-Art nicht gibt, ist
    Rauschen — die Auth-Ablehnung ist die Information."""

    async def fetch(url, headers, params):
        if url.endswith("/openai/v1/models"):
            return 401, {}
        return 404, {}

    out = await discover_models("foundry", "https://res.services.ai.azure.com", "k", fetch)
    assert out["status"] == AI_ACCOUNT_AUTH_FAILED


@pytest.mark.asyncio
async def test_discover_unreachable_only_when_every_candidate_is():
    async def fetch(url, headers, params):
        raise ConnectionError("down")

    out = await discover_models("foundry", "https://res.services.ai.azure.com", "k", fetch)
    assert out["status"] == AI_ACCOUNT_UNREACHABLE
