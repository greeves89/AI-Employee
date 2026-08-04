"""Client-side OAuth for external MCP servers (#426) — pure, I/O-free helpers.

The orchestrator is the OAuth *client* here: it drives ``authorization_code`` +
PKCE (RFC 7636) against an external MCP server's authorization server, stores the
refresh token, and mints fresh access tokens server-side. This module holds only
the pure request/response shaping so it is trivially unit-testable; all network,
DB and crypto live in :mod:`app.services.mcp_oauth_refresh` and the API layer.

Standards followed: OAuth 2.1 (authorization_code + PKCE S256, refresh_token),
RFC 6750 / RFC 9728 (``WWW-Authenticate`` → Protected Resource Metadata),
RFC 8414 (Authorization Server Metadata), RFC 7591 (Dynamic Client Registration),
RFC 8707 (resource indicators).
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urljoin, urlparse

# Refresh a little before the real expiry so an agent never starts a run with a
# token that dies mid-request.
EXPIRY_SKEW_SECONDS = 60


# ---------------------------------------------------------------------------
# PKCE (RFC 7636, S256)
# ---------------------------------------------------------------------------

def gen_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256.

    ``token_urlsafe(64)`` yields ~86 chars, safely inside RFC 7636's 43-128 range.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# WWW-Authenticate parsing (RFC 6750 / RFC 9728)
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def parse_www_authenticate(header: str | None) -> dict[str, str]:
    """Parse a ``Bearer`` challenge into its ``key="value"`` parameters.

    Returns e.g. ``{"error": "invalid_token", "resource_metadata": "https://…"}``.
    Only ``Bearer`` challenges are considered; anything else yields ``{}``.
    """
    if not header:
        return {}
    # A single header may carry multiple challenges; we only care about Bearer.
    if "bearer" not in header.lower():
        return {}
    return {k.lower(): v for k, v in _PARAM_RE.findall(header)}


def resource_metadata_url(header: str | None, resource_url: str | None = None) -> str | None:
    """Best-effort Protected Resource Metadata URL.

    Prefer the explicit ``resource_metadata`` from the challenge; otherwise fall
    back to the RFC 9728 well-known path derived from the resource URL.
    """
    params = parse_www_authenticate(header)
    if params.get("resource_metadata"):
        return params["resource_metadata"]
    if resource_url:
        p = urlparse(resource_url)
        if p.scheme in ("http", "https") and p.netloc:
            base = f"{p.scheme}://{p.netloc}"
            path = p.path.rstrip("/")
            # RFC 9728: metadata path prefixes the resource path under well-known.
            return f"{base}/.well-known/oauth-protected-resource{path}"
    return None


# ---------------------------------------------------------------------------
# Metadata selection (RFC 9728 PRM + RFC 8414 AS metadata)
# ---------------------------------------------------------------------------

def pick_authorization_server(prm: dict | None) -> str | None:
    """First authorization server issuer advertised by a PRM document."""
    if not isinstance(prm, dict):
        return None
    servers = prm.get("authorization_servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        return first if isinstance(first, str) else None
    return None


def as_metadata_urls(issuer: str) -> list[str]:
    """Candidate RFC 8414 metadata URLs for an issuer.

    RFC 8414 inserts the well-known segment *between* host and path; many servers
    (and the OpenID variant) also expose it appended at the end. Try both plus the
    OpenID configuration so discovery works against real-world deployments.
    """
    p = urlparse(issuer)
    if p.scheme not in ("http", "https") or not p.netloc:
        return []
    base = f"{p.scheme}://{p.netloc}"
    path = p.path.rstrip("/")
    out = [
        f"{base}/.well-known/oauth-authorization-server{path}",
        f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration{path}",
        f"{issuer.rstrip('/')}/.well-known/openid-configuration",
    ]
    # De-dup while preserving order.
    seen: set[str] = set()
    return [u for u in out if not (u in seen or seen.add(u))]


def select_endpoints(as_meta: dict | None) -> dict:
    """Extract the endpoints/capabilities we need from AS metadata."""
    m = as_meta if isinstance(as_meta, dict) else {}
    scopes = m.get("scopes_supported")
    return {
        "issuer": m.get("issuer"),
        "authorization_endpoint": m.get("authorization_endpoint"),
        "token_endpoint": m.get("token_endpoint"),
        "registration_endpoint": m.get("registration_endpoint"),
        "grant_types_supported": m.get("grant_types_supported") or [],
        "scopes_supported": scopes if isinstance(scopes, list) else [],
    }


def default_scope(prm: dict | None, as_meta: dict | None) -> str:
    """Pick a scope string, preferring the resource's PRM scopes over the AS's."""
    for src in (prm, as_meta):
        if isinstance(src, dict):
            sc = src.get("scopes_supported")
            if isinstance(sc, list) and sc:
                return " ".join(str(s) for s in sc)
    return ""


# ---------------------------------------------------------------------------
# Request builders
# ---------------------------------------------------------------------------

def build_registration_request(*, redirect_uri: str, client_name: str) -> dict:
    """RFC 7591 Dynamic Client Registration body for a public PKCE client."""
    return {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str = "",
    resource: str | None = None,
) -> str:
    """Build the browser-facing ``authorization_code`` + PKCE authorization URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    if resource:
        params["resource"] = resource
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


def build_token_exchange_data(
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    client_secret: str | None = None,
    resource: str | None = None,
) -> dict:
    """Form body for the ``authorization_code`` → token exchange."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if resource:
        data["resource"] = resource
    return data


def build_refresh_data(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str | None = None,
    scope: str = "",
    resource: str | None = None,
) -> dict:
    """Form body for a ``refresh_token`` grant."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if scope:
        data["scope"] = scope
    if resource:
        data["resource"] = resource
    return data


# ---------------------------------------------------------------------------
# Token response
# ---------------------------------------------------------------------------

def parse_token_response(payload: dict | None) -> dict:
    """Normalize a token endpoint response.

    Returns ``{"access_token", "refresh_token", "expires_at", "scope"}``.
    ``expires_at`` is an absolute epoch second (``None`` if the server gave no
    ``expires_in``). ``refresh_token`` is ``None`` when the server did not rotate
    it — callers keep the previous one in that case.
    """
    p = payload if isinstance(payload, dict) else {}
    access = p.get("access_token")
    if not isinstance(access, str) or not access:
        raise ValueError("token response missing access_token")
    expires_at = None
    exp = p.get("expires_in")
    if isinstance(exp, (int, float)) and exp > 0:
        expires_at = int(time.time()) + int(exp)
    refresh = p.get("refresh_token")
    scope = p.get("scope")
    return {
        "access_token": access,
        "refresh_token": refresh if isinstance(refresh, str) and refresh else None,
        "expires_at": expires_at,
        "scope": scope if isinstance(scope, str) else "",
    }


def is_expired(expires_at: int | float | None, *, skew_seconds: int = EXPIRY_SKEW_SECONDS) -> bool:
    """Whether an access token should be refreshed now.

    A ``None`` expiry is treated as expired: we cannot prove it is still valid, so
    we refresh rather than hand an agent a possibly-dead token.
    """
    if expires_at is None:
        return True
    return time.time() >= float(expires_at) - skew_seconds


def absolute_metadata_url(base_url: str, ref: str) -> str:
    """Resolve a possibly-relative metadata reference against a base URL."""
    return urljoin(base_url, ref)
