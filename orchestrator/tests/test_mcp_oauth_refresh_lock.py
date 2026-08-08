"""Regression tests for issue #462: MCP OAuth refresh race under parallel starts.

When several agents start at once and share an OAuth-MCP server with rotating
refresh tokens, parallel ``refresh_if_needed`` calls must NOT each fire a token
request — the first rotates the refresh token, revoking it for the rest. The fix
serializes the refresh with a per-server PostgreSQL advisory lock and re-checks
the (reloaded) token under the lock, so late callers read the freshly persisted
token instead of replaying a now-revoked refresh token.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.mcp_server import McpServer
from app.services import mcp_oauth_refresh as mor


def _make_server(*, expires_in: int | None) -> McpServer:
    s = McpServer()
    s.id = 7
    s.name = "shared-oauth-mcp"
    s.oauth_enabled = True
    s.oauth_token_endpoint = "https://idp.example/token"
    s.oauth_client_id = "client-abc"
    s.oauth_client_secret_encrypted = None
    s.oauth_scope = "read"
    s.oauth_resource = None
    s.oauth_refresh_token_encrypted = "enc:rt-old"
    s.auth_token_encrypted = "enc:at-old"
    if expires_in is None:
        s.oauth_access_expires_at = None
    else:
        s.oauth_access_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return s


def _pg_session() -> AsyncMock:
    """An AsyncMock session that looks like PostgreSQL to ``_is_postgres``."""
    db = AsyncMock()
    db.bind = MagicMock()
    db.bind.dialect.name = "postgresql"
    db.execute = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _sqlite_session() -> AsyncMock:
    db = AsyncMock()
    db.bind = MagicMock()
    db.bind.dialect.name = "sqlite"
    db.execute = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _enc(token):
    return f"enc:{token}"


def _dec(token):
    return token.split(":", 1)[1] if ":" in token else token


class RefreshLockDialectTests(unittest.IsolatedAsyncioTestCase):
    async def test_lock_acquires_advisory_lock_on_postgres(self):
        db = _pg_session()
        async with mor._refresh_lock(db, 7):
            pass
        db.execute.assert_awaited_once()
        sql = str(db.execute.await_args.args[0])
        params = db.execute.await_args.args[1]
        assert "pg_advisory_xact_lock" in sql
        assert params == {"ns": mor._REFRESH_LOCK_NAMESPACE, "oid": 7}

    async def test_lock_is_noop_off_postgres(self):
        db = _sqlite_session()
        async with mor._refresh_lock(db, 7):
            pass
        db.execute.assert_not_awaited()

    async def test_is_postgres_false_when_bind_missing(self):
        db = AsyncMock()
        db.bind = None
        assert mor._is_postgres(db) is False


class RefreshIfNeededRaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The #503 debounce keeps a process-global "recently verified" cache; clear
        # it so these tests never short-circuit on a verdict left by a sibling test.
        mor._recently_verified.clear()

    async def test_late_caller_skips_token_request_after_winner_refreshed(self):
        """#462 core: if the reload under the lock shows a fresh token, no request."""
        server = _make_server(expires_in=-10)  # currently expired → needs refresh

        db = _pg_session()

        # Simulate the winner having committed a fresh token: db.refresh() reloads
        # the row with a valid access token and a rotated refresh token.
        async def _reload(obj):
            obj.auth_token_encrypted = "enc:at-fresh"
            obj.oauth_refresh_token_encrypted = "enc:rt-rotated"
            obj.oauth_access_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        db.refresh.side_effect = _reload

        with patch.object(mor, "perform_token_request", new=AsyncMock()) as ptr:
            ok = await mor.refresh_if_needed(server, db)

        assert ok is True
        ptr.assert_not_awaited()          # did NOT replay the rotated refresh token
        db.commit.assert_awaited()        # committed to release the lock (no writes pending)

    async def test_winner_performs_refresh_and_commits(self):
        """Still-expired after reload → this caller performs the refresh."""
        server = _make_server(expires_in=-10)
        db = _pg_session()
        db.refresh.side_effect = None  # reload leaves it expired (still needs refresh)

        parsed = {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
            "scope": "read",
        }
        with patch.object(mor, "perform_token_request", new=AsyncMock(return_value=parsed)) as ptr, \
                patch.object(mor, "decrypt_token", side_effect=_dec), \
                patch.object(mor, "encrypt_token", side_effect=_enc):
            ok = await mor.refresh_if_needed(server, db)

        assert ok is True
        ptr.assert_awaited_once()
        db.commit.assert_awaited_once()   # commit persists AND releases the lock
        assert server.auth_token_encrypted == "enc:at-new"
        assert server.oauth_refresh_token_encrypted == "enc:rt-new"

    async def test_valid_token_returns_without_taking_lock(self):
        """Fast path: a still-valid token must not touch the DB/lock at all."""
        server = _make_server(expires_in=3600)  # valid for another hour
        db = _pg_session()
        with patch.object(mor, "perform_token_request", new=AsyncMock()) as ptr:
            ok = await mor.refresh_if_needed(server, db)
        assert ok is True
        ptr.assert_not_awaited()
        db.execute.assert_not_awaited()   # no advisory lock acquired

    async def test_refresh_failure_releases_lock_and_keeps_stale_token(self):
        """A failed token request must release the lock and never raise."""
        server = _make_server(expires_in=-10)
        db = _pg_session()
        db.refresh.side_effect = None
        with patch.object(mor, "perform_token_request",
                          new=AsyncMock(side_effect=mor.OAuthTokenError("boom"))), \
                patch.object(mor, "decrypt_token", side_effect=_dec):
            ok = await mor.refresh_if_needed(server, db)
        assert ok is True                 # stale token still present → usable
        db.commit.assert_awaited()        # committed to release the lock


if __name__ == "__main__":
    unittest.main()
