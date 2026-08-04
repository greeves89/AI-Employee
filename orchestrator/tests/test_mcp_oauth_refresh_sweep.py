"""Tests for issue #488: periodic sweep that refreshes OAuth-MCP tokens on a timer.

Before #488 ``refresh_if_needed`` ran only while building a new agent container, so
a stored access token expired within ~1h and every agent lost the server until it
was recreated. ``refresh_all_oauth_servers`` iterates the ``oauth_enabled`` servers
and refreshes each, so the persisted token stays valid between container creations.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.mcp_server import McpServer
from app.services import mcp_oauth_refresh as mor


def _server(server_id: int, name: str) -> McpServer:
    s = McpServer()
    s.id = server_id
    s.name = name
    s.oauth_enabled = True
    return s


def _session_returning(servers) -> AsyncMock:
    """An AsyncMock session whose execute(...).scalars().all() yields ``servers``."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = servers
    db.execute = AsyncMock(return_value=result)
    return db


class RefreshAllOAuthServersTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_refresh_if_needed_for_every_server_and_counts_usable(self):
        servers = [_server(1, "a"), _server(2, "b"), _server(3, "c")]
        db = _session_returning(servers)

        # b fails to refresh (returns False), a and c succeed.
        results = {1: True, 2: False, 3: True}

        async def _fake_refresh(server, session):
            assert session is db
            return results[server.id]

        with patch.object(mor, "refresh_if_needed", side_effect=_fake_refresh) as rin:
            usable = await mor.refresh_all_oauth_servers(db)

        assert usable == 2
        assert rin.await_count == 3
        called_ids = sorted(c.args[0].id for c in rin.await_args_list)
        assert called_ids == [1, 2, 3]

    async def test_only_selects_oauth_enabled_servers(self):
        db = _session_returning([])
        with patch.object(mor, "refresh_if_needed", new=AsyncMock()):
            await mor.refresh_all_oauth_servers(db)
        db.execute.assert_awaited_once()
        # The WHERE clause must filter on oauth_enabled so non-OAuth servers are skipped.
        sql = str(db.execute.await_args.args[0]).lower()
        assert "oauth_enabled" in sql

    async def test_one_failing_server_does_not_abort_the_sweep(self):
        servers = [_server(1, "a"), _server(2, "boom"), _server(3, "c")]
        db = _session_returning(servers)

        async def _fake_refresh(server, session):
            if server.id == 2:
                raise RuntimeError("unexpected explosion")
            return True

        with patch.object(mor, "refresh_if_needed", side_effect=_fake_refresh) as rin:
            usable = await mor.refresh_all_oauth_servers(db)

        # server 2 raised, but 1 and 3 were still refreshed and counted.
        assert usable == 2
        assert rin.await_count == 3


if __name__ == "__main__":
    unittest.main()
