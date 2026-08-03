"""Model discovery + connection health for AI accounts (#435).

A single request against the provider's model list covers all three gaps the
issue describes at once: the result *is* the pick-list, a typed model name can be
validated against it, and whether the request succeeded *is* the connection
state. ``GET /v1/models`` is the OpenAI-compatible standard (OpenAI, LiteLLM,
Groq, Together, vLLM, Ollama, LM Studio); Anthropic exposes the same path with a
different auth header, and Google uses ``/v1beta/models``.

This module is pure and network-free: :func:`discover_models` takes an injected
``fetch`` coroutine so it is unit-testable without httpx or a live provider. The
API layer supplies a real, SSRF-guarded fetch.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

# Status vocabulary is shared verbatim with the MCP health check so the UI can
# reuse one renderer. ``unsupported`` is added for providers with no discovery
# endpoint (e.g. Bedrock), where a manual model list stays the only option.
AI_ACCOUNT_OK = "ok"
AI_ACCOUNT_AUTH_FAILED = "auth_failed"
AI_ACCOUNT_UNREACHABLE = "unreachable"
AI_ACCOUNT_PROTOCOL_ERROR = "protocol_error"
AI_ACCOUNT_UNSUPPORTED = "unsupported"

# Providers speaking the OpenAI ``GET {base}/v1/models`` shape with a Bearer key.
_OPENAI_COMPATIBLE = {"openai", "azure-openai", "ollama", "lm-studio"}

# Fallback base URLs for hosted providers, so discovery works before an operator
# types a custom endpoint. Self-hosted providers (ollama, lm-studio) have none.
_DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
}

# fetch(url, headers, params) -> (status_code | None, json_body | None)
# status_code None signals a transport failure (the caller maps it to UNREACHABLE).
FetchFn = Callable[
    [str, dict, dict],
    Awaitable[tuple[Optional[int], Optional[dict]]],
]


def _base_url(provider_type: str, api_endpoint: str | None) -> str:
    raw = (api_endpoint or _DEFAULT_ENDPOINTS.get(provider_type) or "").strip()
    return raw.rstrip("/")


def build_discovery_request(
    provider_type: str, api_endpoint: str | None, api_key: str | None
) -> dict | None:
    """Build the discovery HTTP request for a provider, or None if unsupported.

    Returns ``{"url", "headers", "params"}``. None means the provider exposes no
    model-list endpoint we know how to call — the caller reports ``unsupported``
    and keeps manual model entry.
    """
    base = _base_url(provider_type, api_endpoint)
    if not base:
        return None

    if provider_type == "anthropic":
        return {
            "url": f"{base}/v1/models",
            "headers": {
                "x-api-key": api_key or "",
                "anthropic-version": "2023-06-01",
            },
            "params": {},
        }

    if provider_type == "google":
        return {
            "url": f"{base}/v1beta/models",
            "headers": {},
            "params": {"key": api_key} if api_key else {},
        }

    if provider_type in _OPENAI_COMPATIBLE:
        # If the endpoint already ends in /v1, append only /models (LiteLLM and
        # some gateways are configured with the /v1 base baked in).
        path = "/models" if base.endswith("/v1") else "/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        return {"url": f"{base}{path}", "headers": headers, "params": {}}

    return None


def parse_models(payload: dict, provider_type: str) -> list[dict]:
    """Extract ``[{"id", "label"}]`` from a provider's model-list response.

    Handles the OpenAI ``{"data": [{"id": ...}]}`` shape and Google's
    ``{"models": [{"name": "models/...", "displayName": ...}]}``. Unknown/empty
    entries are skipped; ids are de-duplicated preserving order.
    """
    out: list[dict] = []
    seen: set[str] = set()

    if provider_type == "google":
        items = payload.get("models") or []
        for m in items:
            if not isinstance(m, dict):
                continue
            raw = (m.get("name") or "").split("/")[-1].strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            out.append({"id": raw, "label": (m.get("displayName") or raw)})
        return out

    items = payload.get("data") or []
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = (m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "label": mid})
    return out


def classify_http_status(status_code: int) -> str:
    if status_code == 200:
        return AI_ACCOUNT_OK
    if status_code in (401, 403):
        return AI_ACCOUNT_AUTH_FAILED
    return AI_ACCOUNT_PROTOCOL_ERROR


async def discover_models(
    provider_type: str,
    api_endpoint: str | None,
    api_key: str | None,
    fetch: FetchFn,
) -> dict:
    """Run discovery and return ``{"status", "models", "error"}``.

    Never raises: a transport failure becomes ``unreachable``, an auth rejection
    ``auth_failed``, any other non-200 ``protocol_error``. The ``fetch`` callable
    isolates the network so this stays unit-testable.
    """
    request = build_discovery_request(provider_type, api_endpoint, api_key)
    if request is None:
        return {
            "status": AI_ACCOUNT_UNSUPPORTED,
            "models": [],
            "error": "This provider has no model-discovery endpoint; enter models manually.",
        }

    try:
        status_code, body = await fetch(
            request["url"], request["headers"], request["params"]
        )
    except Exception:
        status_code, body = None, None

    if status_code is None:
        return {
            "status": AI_ACCOUNT_UNREACHABLE,
            "models": [],
            "error": "Could not reach the provider endpoint.",
        }

    status = classify_http_status(status_code)
    if status == AI_ACCOUNT_OK:
        return {"status": status, "models": parse_models(body or {}, provider_type), "error": None}
    if status == AI_ACCOUNT_AUTH_FAILED:
        return {"status": status, "models": [], "error": f"Authentication failed ({status_code})."}
    return {"status": status, "models": [], "error": f"Provider returned HTTP {status_code}."}
