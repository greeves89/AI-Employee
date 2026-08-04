from types import SimpleNamespace

import pytest

from app.services.mcp_agent_health import (
    AGENT_MCP_CONNECTED,
    AGENT_MCP_FAILED,
    AGENT_MCP_NEEDS_AUTH,
    AGENT_MCP_UNKNOWN,
    collect_agent_mcp_health,
    parse_claude_mcp_list,
    rollup_statuses,
)

SAMPLE_OUTPUT = """Checking MCP server health...

memory: node /opt/mcp/memory-server.mjs - ✓ Connected
orchestrator: node /opt/mcp/orchestrator-server.mjs - ✓ Connected
composio: https://mcp.composio.dev/xyz (HTTP) - ✗ Failed to connect
n8n: https://n8n.example.test/mcp (HTTP) - Needs authentication
"""


def test_parse_classifies_each_server_line():
    parsed = parse_claude_mcp_list(SAMPLE_OUTPUT)

    assert parsed["memory"] == AGENT_MCP_CONNECTED
    assert parsed["orchestrator"] == AGENT_MCP_CONNECTED
    assert parsed["composio"] == AGENT_MCP_FAILED
    assert parsed["n8n"] == AGENT_MCP_NEEDS_AUTH


def test_parse_ignores_banner_and_blank_lines():
    parsed = parse_claude_mcp_list(SAMPLE_OUTPUT)
    # Only real server lines are kept; the "Checking..." banner is dropped.
    assert set(parsed) == {"memory", "orchestrator", "composio", "n8n"}


def test_parse_ignores_lines_without_a_verdict():
    # Some CLI versions print just "name: command" with no " - <status>".
    parsed = parse_claude_mcp_list("foo: node /opt/mcp/foo.mjs\nbar: url - ✓ Connected\n")
    assert parsed == {"bar": AGENT_MCP_CONNECTED}


def test_parse_handles_empty_output():
    assert parse_claude_mcp_list("") == {}


def test_rollup_prefers_failure_over_auth_over_connected():
    assert rollup_statuses([AGENT_MCP_CONNECTED, AGENT_MCP_FAILED]) == AGENT_MCP_FAILED
    assert rollup_statuses([AGENT_MCP_CONNECTED, AGENT_MCP_NEEDS_AUTH]) == AGENT_MCP_NEEDS_AUTH
    assert rollup_statuses([AGENT_MCP_CONNECTED, AGENT_MCP_CONNECTED]) == AGENT_MCP_CONNECTED
    assert rollup_statuses([AGENT_MCP_UNKNOWN]) == AGENT_MCP_UNKNOWN


def test_rollup_none_when_no_agent_reported():
    assert rollup_statuses([]) is None


@pytest.mark.asyncio
async def test_collect_aggregates_per_server_across_agents():
    agents = [
        SimpleNamespace(id="a1", name="Alice", container_id="c1"),
        SimpleNamespace(id="a2", name="Bob", container_id="c2"),
    ]
    outputs = {
        "c1": "composio: https://x (HTTP) - ✓ Connected\n",
        "c2": "composio: https://x (HTTP) - ✗ Failed to connect\n",
    }

    async def exec_list(container_id):
        return outputs.get(container_id)

    result = await collect_agent_mcp_health(agents, exec_list, ["composio"])

    assert result["agents_checked"] == 2
    server = result["servers"]["composio"]
    assert server["connected"] == 1
    assert server["failed"] == 1
    # A failure anywhere makes the headline status "failed".
    assert server["agent_status"] == AGENT_MCP_FAILED
    assert {a["agent_id"] for a in server["agents"]} == {"a1", "a2"}


@pytest.mark.asyncio
async def test_collect_is_fail_soft_when_an_agent_check_returns_none():
    agents = [
        SimpleNamespace(id="a1", name="Alice", container_id="c1"),
        SimpleNamespace(id="a2", name="Bob", container_id="c2"),
    ]

    async def exec_list(container_id):
        if container_id == "c2":
            return None  # agent unreachable / command failed
        return "composio: https://x (HTTP) - ✓ Connected\n"

    result = await collect_agent_mcp_health(agents, exec_list, ["composio"])

    assert result["agents_checked"] == 1
    assert result["servers"]["composio"]["connected"] == 1


@pytest.mark.asyncio
async def test_collect_ignores_servers_not_in_wanted_set():
    agents = [SimpleNamespace(id="a1", name="Alice", container_id="c1")]

    async def exec_list(container_id):
        return "memory: node x - ✓ Connected\ncomposio: url - ✗ Failed\n"

    result = await collect_agent_mcp_health(agents, exec_list, ["composio"])

    # Built-in "memory" is not a registered external server → excluded.
    assert set(result["servers"]) == {"composio"}
    assert result["servers"]["composio"]["agent_status"] == AGENT_MCP_FAILED


@pytest.mark.asyncio
async def test_collect_reports_none_status_for_server_no_agent_sees():
    agents = [SimpleNamespace(id="a1", name="Alice", container_id="c1")]

    async def exec_list(container_id):
        return "composio: url - ✓ Connected\n"

    result = await collect_agent_mcp_health(agents, exec_list, ["composio", "slack"])

    assert result["servers"]["slack"]["agent_status"] is None
    assert result["servers"]["slack"]["agents"] == []
