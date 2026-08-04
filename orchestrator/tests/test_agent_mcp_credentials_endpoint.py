"""Tests for #488 Phase 2: GET /agents/{id}/mcp-credentials.

CUSTOM_MCP_AUTH is only ever computed once, at container-creation time, so a
running agent has no way to learn that an OAuth token rotated. This endpoint
lets the agent's own periodic refresh loop pull the current credentials
on demand — same computation _get_custom_mcp_env already does for a new
container, just callable repeatedly.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.api.agents import get_agent_mcp_credentials


def _manager(agent_config: dict, mcp_env: dict) -> MagicMock:
    manager = MagicMock()
    agent = MagicMock()
    agent.config = agent_config
    manager._get_agent = AsyncMock(return_value=agent)
    manager._get_custom_mcp_env = AsyncMock(return_value=mcp_env)
    return manager


class GetAgentMcpCredentialsTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_servers_auth_and_headers(self):
        manager = _manager(
            agent_config={"integrations": ["microsoft"]},
            mcp_env={
                "CUSTOM_MCP_SERVERS": '{"srv": "https://x/mcp"}',
                "CUSTOM_MCP_AUTH": '{"srv": "fresh-token"}',
                "CUSTOM_MCP_HEADERS": '{"srv": {"X-Api-Key": "k"}}',
            },
        )

        result = await get_agent_mcp_credentials(
            "agent-1", manager=manager, agent_auth={"agent_id": "agent-1"}
        )

        assert result == {
            "servers": {"srv": "https://x/mcp"},
            "auth": {"srv": "fresh-token"},
            "headers": {"srv": {"X-Api-Key": "k"}},
        }
        manager._get_custom_mcp_env.assert_awaited_once_with(
            agent_config={"integrations": ["microsoft"]},
            agent_id="agent-1",
            agent_integrations=["microsoft"],
        )

    async def test_defaults_to_empty_dicts_when_no_custom_servers(self):
        manager = _manager(agent_config={}, mcp_env={})

        result = await get_agent_mcp_credentials(
            "agent-1", manager=manager, agent_auth={"agent_id": "agent-1"}
        )

        assert result == {"servers": {}, "auth": {}, "headers": {}}

    async def test_rejects_token_for_a_different_agent(self):
        manager = _manager(agent_config={}, mcp_env={})

        with self.assertRaises(HTTPException) as ctx:
            await get_agent_mcp_credentials(
                "agent-1", manager=manager, agent_auth={"agent_id": "agent-2"}
            )
        assert ctx.exception.status_code == 403
        manager._get_agent.assert_not_awaited()

    async def test_404_when_agent_missing(self):
        manager = MagicMock()
        manager._get_agent = AsyncMock(side_effect=ValueError("not found"))

        with self.assertRaises(HTTPException) as ctx:
            await get_agent_mcp_credentials(
                "agent-1", manager=manager, agent_auth={"agent_id": "agent-1"}
            )
        assert ctx.exception.status_code == 404
