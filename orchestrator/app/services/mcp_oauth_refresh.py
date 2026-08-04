"""Execution half of client-side MCP OAuth (#426): token requests + server-side refresh.

Keeps the network/DB/crypto out of the pure :mod:`app.services.mcp_oauth_client`
helpers. Used by the MCP-servers API (code exchange on the OAuth callback) and by
the agent manager (mint a fresh access token just before an agent starts).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token, encrypt_token
from app.models.mcp_server import McpServer
from app.services import mcp_oauth_client as oc

logger = logging.getLogger(__name__)

_TOKEN_TIMEOUT = 15.0

# Namespace for the per-server advisory lock (#462). PostgreSQL's two-int form
# ``pg_advisory_xact_lock(classid, objid)`` keys the lock on (namespace, id) so a
# server id can never collide with another advisory-lock user in the cluster.
# Arbitrary fixed int4 ("mcp\0"); must stay stable across releases.
_REFRESH_LOCK_NAMESPACE = 0x6D63_7000


def _is_postgres(db: AsyncSession) -> bool:
    try:
        return db.bind.dialect.name == "postgresql"
    except Exception:
        return False


@asynccontextmanager
async def _refresh_lock(db: AsyncSession, server_id: int):
    """Serialize concurrent OAuth refreshes for one server across agent sessions.

    On PostgreSQL this takes a *transaction-level* advisory lock keyed on the
    server id (namespaced). When several agents start at once and share an
    OAuth-MCP server with rotating refresh tokens, only the first caller performs
    the token request; the others block here until it commits, then re-read the
    freshly persisted token instead of replaying an already-rotated (revoked)
    refresh token (#462). The lock auto-releases when the caller's transaction
    ends (commit/rollback) — so callers MUST end their transaction after the
    critical section, which :func:`refresh_if_needed` already does.

    On any other backend (e.g. the SQLite unit tests) it is a no-op: there is no
    cross-process race to guard.
    """
    if _is_postgres(db):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :oid)"),
            {"ns": _REFRESH_LOCK_NAMESPACE, "oid": int(server_id)},
        )
    yield


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

    async with _refresh_lock(db, server.id):
        # Re-check under the lock (#462): a concurrent caller may have just
        # refreshed and committed. Reload the row so we observe its committed
        # token and rotated refresh token instead of firing our own request with
        # a now-revoked refresh token.
        try:
            await db.refresh(server)
        except Exception:  # noqa: BLE001 — a fresh reload is best-effort
            pass
        expires_at = server.oauth_access_expires_at
        epoch = expires_at.timestamp() if expires_at else None
        if server.auth_token_encrypted and not oc.is_expired(epoch):
            # A concurrent winner already refreshed; release the lock promptly.
            await _release(db)
            return True

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
            await _release(db)
            return bool(server.auth_token_encrypted)

        apply_token_to_server(server, parsed)
        try:
            await db.commit()  # persists the token AND releases the advisory lock
        except Exception:
            await db.rollback()
            logger.warning("Could not persist refreshed MCP token for server %s", server.name)
            return bool(server.auth_token_encrypted)
        return True


async def refresh_all_oauth_servers(db: AsyncSession) -> int:
    """Refresh every OAuth-enabled MCP server whose access token is (near) expired.

    Intended for a periodic background sweep (#488). Before this, ``refresh_if_needed``
    ran at exactly one moment — while building the environment for a new agent
    container — so a stored access token expired within roughly an hour and every
    agent lost the server until its container was recreated. Running this on a timer
    keeps the persisted token valid so freshly created and restarted agents receive
    a live token.

    ``refresh_if_needed`` is a no-op for a server whose token is still valid and
    never raises, so this is cheap to run often. It commits per server (releasing
    the per-server advisory lock each time), which is safe alongside agent startup.
    Returns the number of servers holding a usable token after the sweep.
    """
    result = await db.execute(select(McpServer).where(McpServer.oauth_enabled.is_(True)))
    servers = result.scalars().all()
    usable = 0
    for server in servers:
        try:
            if await refresh_if_needed(server, db):
                usable += 1
        except Exception as exc:  # noqa: BLE001 — one bad server must not abort the sweep
            logger.warning(
                "MCP OAuth sweep: refresh raised for server %s: %s",
                getattr(server, "name", "?"), exc,
            )
            await db.rollback()
    return usable


async def _release(db: AsyncSession) -> None:
    """End the current transaction to release a held advisory lock, best-effort.

    Uses ``commit`` (not ``rollback``) to end the transaction: the session runs
    with ``expire_on_commit=False``, so committing releases the advisory lock
    without expiring the loaded ORM instances — the caller (``agent_manager``)
    reads ``server.auth_token_encrypted`` right after in a non-async context, and
    a rollback would expire it and trigger an illegal async lazy-load. No writes
    are pending on the release paths, so the commit flushes nothing meaningful.
    """
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
