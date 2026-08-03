"""Execution half of client-side MCP OAuth (#426): token requests + server-side refresh.

Keeps the network/DB/crypto out of the pure :mod:`app.services.mcp_oauth_client`
helpers. Used by the MCP-servers API (code exchange on the OAuth callback) and by
the agent manager (mint a fresh access token just before an agent starts).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token, encrypt_token
from app.models.mcp_server import McpServer
from app.services import mcp_oauth_client as oc

logger = logging.getLogger(__name__)

_TOKEN_TIMEOUT = 15.0


class OAuthTokenError(Exception):
    """A token endpoint call failed (network, non-2xx, or malformed response)."""


async def perform_token_request(token_endpoint: str, data: dict) -> dict:
    """POST a form to an OAuth token endpoint and return a parsed token response.

    SSRF-guarded (same DNS-resolving guard the manual tools/call path uses) and
    fail-closed. Returns the dict from :func:`mcp_oauth_client.parse_token_response`.
    """
    # Lazy import avoids a circular dependency (the API module imports this one).
    from app.api.mcp_servers import _assert_mcp_url_allowed

    await _assert_mcp_url_allowed(token_endpoint)
    try:
        async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT) as client:
            resp = await client.post(
                token_endpoint,
                data=data,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
    except httpx.RequestError as exc:
        raise OAuthTokenError(f"token endpoint unreachable: {exc}") from exc

    if resp.status_code >= 400:
        # Surface the OAuth error code when present, without leaking the body.
        detail = ""
        try:
            body = resp.json()
            detail = body.get("error") or body.get("error_description") or ""
        except Exception:
            pass
        raise OAuthTokenError(f"token endpoint returned {resp.status_code}"
                              + (f" ({detail})" if detail else ""))
    try:
        payload = resp.json()
    except Exception as exc:
        raise OAuthTokenError("token endpoint returned non-JSON") from exc
    try:
        return oc.parse_token_response(payload)
    except ValueError as exc:
        raise OAuthTokenError(str(exc)) from exc


def apply_token_to_server(server: McpServer, parsed: dict) -> None:
    """Store a parsed token response on the server row (in-memory; caller commits).

    The access token goes into ``auth_token_encrypted`` so it reaches agents
    unchanged. A rotated refresh token replaces the old one; if the server did not
    return one, the previous refresh token is kept.
    """
    server.auth_token_encrypted = encrypt_token(parsed["access_token"])
    if parsed.get("refresh_token"):
        server.oauth_refresh_token_encrypted = encrypt_token(parsed["refresh_token"])
    expires_at = parsed.get("expires_at")
    server.oauth_access_expires_at = (
        datetime.fromtimestamp(expires_at, tz=timezone.utc) if expires_at else None
    )
    if parsed.get("scope"):
        server.oauth_scope = parsed["scope"]


async def refresh_if_needed(server: McpServer, db: AsyncSession) -> bool:
    """Ensure ``server`` has a non-expired access token, refreshing if necessary.

    Returns True if the server now holds a usable (fresh or still-valid) access
    token, False if it could not be refreshed (no refresh token, or the grant
    failed). Never raises — a refresh failure must not break agent startup or a
    listing; the stale token simply stays and the server's own health check will
    flag the eventual 401.
    """
    if not getattr(server, "oauth_enabled", False):
        return bool(server.auth_token_encrypted)

    expires_at = server.oauth_access_expires_at
    epoch = expires_at.timestamp() if expires_at else None
    if server.auth_token_encrypted and not oc.is_expired(epoch):
        return True

    if not server.oauth_refresh_token_encrypted or not server.oauth_token_endpoint:
        return bool(server.auth_token_encrypted)

    try:
        refresh_token = decrypt_token(server.oauth_refresh_token_encrypted)
        client_secret = (
            decrypt_token(server.oauth_client_secret_encrypted)
            if server.oauth_client_secret_encrypted else None
        )
        data = oc.build_refresh_data(
            refresh_token=refresh_token,
            client_id=server.oauth_client_id or "",
            client_secret=client_secret,
            scope=server.oauth_scope or "",
            resource=server.oauth_resource or None,
        )
        parsed = await perform_token_request(server.oauth_token_endpoint, data)
    except Exception as exc:  # noqa: BLE001 — see docstring: never break the caller
        logger.warning("MCP OAuth refresh failed for server %s: %s", server.name, exc)
        return bool(server.auth_token_encrypted)

    apply_token_to_server(server, parsed)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Could not persist refreshed MCP token for server %s", server.name)
        return bool(server.auth_token_encrypted)
    return True
