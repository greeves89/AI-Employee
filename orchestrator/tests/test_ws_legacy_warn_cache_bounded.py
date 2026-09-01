"""Tests for the legacy ``token=`` warn-debounce cache in app/api/ws.py (issue #662).

The debounce dict gets one entry per distinct 16-char token prefix and used to be
never pruned, so it grew with every re-login and every token refresh in a process
that runs for weeks. These tests drive the real ``_authenticate_ws`` legacy branch
so that removing the pruning call from that branch fails them -- testing the prune
helper on its own would still pass with the wiring gone.
"""
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import ws


def _token(i: int) -> str:
    """A token whose first 16 chars -- the part used as the cache key -- are unique."""
    return f"{i:016d}"


def _patch_session_factory():
    @asynccontextmanager
    async def _factory():
        yield AsyncMock()

    return patch.object(ws, "async_session_factory", _factory)


async def _authenticate_with(token: str):
    websocket = MagicMock()
    websocket.close = AsyncMock()
    websocket.state = MagicMock()
    with _patch_session_factory(), patch.object(
        ws, "get_current_user_ws", AsyncMock(return_value=MagicMock(id="user-1"))
    ):
        return await ws._authenticate_ws(websocket, token=token)


class LegacyWarnCacheBoundedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ws._legacy_token_warned.clear()

    tearDown = setUp

    async def test_expired_entries_are_pruned_when_a_new_warning_is_recorded(self):
        for i in range(500):
            ws._legacy_token_warned[f"stale-{i}"] = 0.0
        with patch.object(ws.time, "monotonic", return_value=ws._LEGACY_WARN_COOLDOWN + 1):
            await _authenticate_with("fresh-token-abcdefgh")
        # Every seeded entry is older than the cooldown, so only the new one survives.
        self.assertEqual(len(ws._legacy_token_warned), 1)
        self.assertNotIn("stale-0", ws._legacy_token_warned)

    async def test_cache_never_exceeds_the_hard_cap(self):
        # A frozen clock means nothing ever expires -- only the size cap can bound this.
        # Tokens must differ inside the first 16 chars, otherwise they share a key
        # and the cap is never reached.
        overshoot = 50
        with patch.object(ws.time, "monotonic", return_value=1000.0):
            for i in range(ws._LEGACY_WARN_MAX_ENTRIES + overshoot):
                await _authenticate_with(_token(i))
        self.assertEqual(len(ws._legacy_token_warned), ws._LEGACY_WARN_MAX_ENTRIES)

    async def test_oldest_entry_is_evicted_first(self):
        with patch.object(ws.time, "monotonic", return_value=1000.0):
            for i in range(ws._LEGACY_WARN_MAX_ENTRIES + 1):
                await _authenticate_with(_token(i))
        self.assertNotIn(_token(0), ws._legacy_token_warned)
        self.assertIn(_token(ws._LEGACY_WARN_MAX_ENTRIES), ws._legacy_token_warned)

    async def test_debounce_still_suppresses_a_repeat_warning(self):
        # Pruning must not defeat the point of the cache: the same token inside one
        # cooldown window still warns exactly once.
        with patch.object(ws.time, "monotonic", return_value=1000.0):
            with self.assertLogs(ws.logger, level="WARNING") as captured:
                await _authenticate_with("same-token-abcdefgh")
                await _authenticate_with("same-token-abcdefgh")
        legacy = [m for m in captured.output if "legacy token=" in m]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(len(ws._legacy_token_warned), 1)

    async def test_warning_returns_after_the_cooldown_expires(self):
        with patch.object(ws.time, "monotonic", return_value=1000.0):
            await _authenticate_with("same-token-abcdefgh")
        with patch.object(ws.time, "monotonic", return_value=1000.0 + ws._LEGACY_WARN_COOLDOWN + 1):
            with self.assertLogs(ws.logger, level="WARNING") as captured:
                await _authenticate_with("same-token-abcdefgh")
        self.assertTrue(any("legacy token=" in m for m in captured.output))


if __name__ == "__main__":
    unittest.main()
