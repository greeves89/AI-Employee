"""Tests for issue #503: per-server debounce in ``refresh_if_needed``.

With many agents polling ``/mcp-credentials`` on a 300s cycle, ``refresh_if_needed``
ran once per agent per server per tick. When a token crossed the expiry-skew
threshold, every one of those calls contended on the same per-server advisory lock
for one refresh. A short in-process debounce records a "usable" verdict per server
so only the first caller per window does the expiry-check / lock / token dance; the
rest short-circuit. The window MUST stay below ``EXPIRY_SKEW_SECONDS`` so a debounced
verdict can never outlive the validity it was based on.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.mcp_server import McpServer
from app.services import mcp_oauth_client as oc
from app.services import mcp_oauth_refresh as mor


def _make_server(*, expires_in: int | None, server_id: int = 42) -> McpServer:
    s = McpServer()
    s.id = server_id
    s.name = "debounced-oauth-mcp"
    s.oauth_enabled = True
    s.oauth_token_endpoint = "https://idp.example/token"
    s.oauth_client_id = "client-abc"
    s.oauth_client_secret_encrypted = None
    s.oauth_scope = "read"
    s.oauth_resource = None
    s.oauth_refresh_token_encrypted = "enc:rt-old"
    s.auth_token_encrypted = "enc:at-old"
    s.oauth_access_expires_at = (
        None if expires_in is None
        else datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    )
    return s


def _pg_session() -> AsyncMock:
    db = AsyncMock()
    db.bind = MagicMock()
    db.bind.dialect.name = "postgresql"
    db.execute = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class DebounceWindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mor._recently_verified.clear()

    def test_debounce_window_is_below_expiry_skew(self):
        # Core safety invariant: a token confirmed "not expiring within the skew"
        # cannot actually expire before the debounce elapses.
        assert mor._REFRESH_DEBOUNCE_SECONDS < oc.EXPIRY_SKEW_SECONDS

    async def test_second_call_short_circuits_after_valid_verdict(self):
        """A still-valid token is verified once; the next call skips the DB entirely."""
        server = _make_server(expires_in=3600)
        db = _pg_session()

        with patch.object(mor, "perform_token_request", new=AsyncMock()) as ptr:
            assert await mor.refresh_if_needed(server, db) is True
            db.execute.assert_not_awaited()  # fast path, no lock
            # Second call within the window: debounced, no expiry re-check, no lock.
            assert await mor.refresh_if_needed(server, db) is True

        ptr.assert_not_awaited()
        assert mor._debounced(server.id) is True

    async def test_second_call_short_circuits_after_a_refresh(self):
        """After one caller refreshes, sibling callers skip the token request."""
        server = _make_server(expires_in=-10)  # expired → first call refreshes
        db = _pg_session()
        db.refresh.side_effect = None  # reload leaves it expired

        parsed = {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
            "scope": "read",
        }
        with patch.object(mor, "perform_token_request",
                          new=AsyncMock(return_value=parsed)) as ptr, \
                patch.object(mor, "decrypt_token", side_effect=lambda t: t.split(":", 1)[1]), \
                patch.object(mor, "encrypt_token", side_effect=lambda t: f"enc:{t}"):
            assert await mor.refresh_if_needed(server, db) is True
            ptr.assert_awaited_once()
            # Second call within the window must NOT fire another token request.
            assert await mor.refresh_if_needed(server, db) is True
            ptr.assert_awaited_once()

    async def test_expired_debounce_re_checks(self):
        """Once the window elapses, the next call re-evaluates instead of trusting the cache."""
        server = _make_server(expires_in=3600)
        db = _pg_session()

        with patch.object(mor, "perform_token_request", new=AsyncMock()):
            assert await mor.refresh_if_needed(server, db) is True
        assert mor._debounced(server.id) is True

        # Simulate the window having elapsed.
        mor._recently_verified[server.id] = 0.0
        assert mor._debounced(server.id) is False

    async def test_failed_refresh_is_not_debounced(self):
        """A caller that could not refresh must not leave a 'usable' verdict behind."""
        server = _make_server(expires_in=-10)
        db = _pg_session()
        db.refresh.side_effect = None
        with patch.object(mor, "perform_token_request",
                          new=AsyncMock(side_effect=mor.OAuthTokenError("boom"))), \
                patch.object(mor, "decrypt_token", side_effect=lambda t: t.split(":", 1)[1]):
            await mor.refresh_if_needed(server, db)
        # Degraded state (stale token, refresh failed) → next caller must retry.
        assert mor._debounced(server.id) is False

    async def test_disabled_server_is_not_debounced(self):
        server = _make_server(expires_in=3600)
        server.oauth_enabled = False
        db = _pg_session()
        assert await mor.refresh_if_needed(server, db) is True
        assert mor._debounced(server.id) is False


if __name__ == "__main__":
    unittest.main()
