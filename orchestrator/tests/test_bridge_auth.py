"""Unit tests for app.telegram._bridge_auth (review findings PR #254)."""

import asyncio
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Stub the DB session before importing bridge_auth so it never tries to connect
_db_session_stub = types.ModuleType("app.db.session")
_db_session_stub.async_session_factory = MagicMock()
sys.modules.setdefault("app.db.session", _db_session_stub)

from app.telegram._bridge_auth import (  # noqa: E402
    authed_client,
    bridge_token,
)
import app.telegram._bridge_auth as _ba  # noqa: E402


def _patch_factory(user):
    """Patch async_session_factory on the module under test; return (patcher, session)."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(_ba, "async_session_factory", MagicMock(return_value=ctx)), session


class BridgeTokenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _ba._TOKEN_CACHE = None
        _ba._TOKEN_EXPIRY = 0.0
        _ba._cache_lock = asyncio.Lock()

    async def test_raises_when_no_admin(self):
        """RuntimeError must be raised (not None returned) when no admin exists."""
        patcher, _ = _patch_factory(user=None)
        with patcher:
            with self.assertRaises(RuntimeError):
                await bridge_token()

    async def test_token_cached_on_second_call(self):
        """DB must be hit only once; second call returns the cached token."""
        from app.models.user import UserRole

        user = MagicMock()
        user.id = "u1"
        user.role = UserRole.ADMIN  # real enum; .value already == "admin"

        patcher, session = _patch_factory(user=user)
        with patcher:
            t1 = await bridge_token()
            t2 = await bridge_token()

        self.assertEqual(t1, t2)
        self.assertEqual(session.execute.call_count, 1, "DB queried more than once")

    async def test_expired_cache_re_fetches(self):
        """Expired token must trigger a fresh DB query."""
        _ba._TOKEN_CACHE = "old-token"
        _ba._TOKEN_EXPIRY = time.monotonic() - 1

        from app.models.user import UserRole

        user = MagicMock()
        user.id = "u2"
        user.role = UserRole.ADMIN  # real enum; .value already == "admin"

        patcher, session = _patch_factory(user=user)
        with patcher:
            token = await bridge_token()

        self.assertNotEqual(token, "old-token")
        self.assertEqual(session.execute.call_count, 1)

    async def test_authed_client_sets_auth_header(self):
        """authed_client must always include an Authorization header."""
        _ba._TOKEN_CACHE = "cached-tok"
        _ba._TOKEN_EXPIRY = time.monotonic() + 9999

        client = await authed_client()
        self.assertIn("authorization", client.headers)
        self.assertIn("cached-tok", client.headers["authorization"])
        await client.aclose()

    async def test_authed_client_propagates_runtime_error(self):
        """authed_client must NOT silently send unauthed requests when no admin exists."""
        patcher, _ = _patch_factory(user=None)
        with patcher:
            with self.assertRaises(RuntimeError):
                await authed_client()


if __name__ == "__main__":
    unittest.main()
