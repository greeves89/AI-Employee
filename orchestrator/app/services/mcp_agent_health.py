"""Agent-side MCP connection health (#425 Phase 2).

The orchestrator's own discovery check (``app/api/mcp_servers.py``) only proves
that the *orchestrator* can reach an MCP server. Every agent container registers
the same external servers via ``claude mcp add`` and may see a different result:
a per-agent auth token can be rejected (401) on a URL the orchestrator reaches
anonymously, or a container's egress may be firewalled differently. This module
surfaces that second, independent signal by running ``claude mcp list`` inside
each running agent and parsing its per-server connectivity verdict, so a green
orchestrator status is never mistaken for "agents can actually use it".

The parser is a pure function so it can be unit-tested without Docker; the
collector takes an injected ``exec_list`` callable for the same reason.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable

AGENT_MCP_CONNECTED = "connected"
AGENT_MCP_FAILED = "failed"
AGENT_MCP_NEEDS_AUTH = "needs_auth"
AGENT_MCP_UNKNOWN = "unknown"

# Precedence for rolling many agents' verdicts for one server into a single
# headline status: a real failure is the most actionable, so it wins; an auth
# problem is next; "connected" only when nothing worse was observed.
_ROLLUP_ORDER = [
    AGENT_MCP_FAILED,
    AGENT_MCP_NEEDS_AUTH,
    AGENT_MCP_CONNECTED,
    AGENT_MCP_UNKNOWN,
]

# A `claude mcp list` server line: "<name>: <command-or-url> - <verdict>".
# Server names are sanitized to [A-Za-z0-9_-] before registration, so the name
# never contains the ": " that separates it from the rest of the line.
_MCP_LIST_LINE = re.compile(r"^(?P<name>[A-Za-z0-9_-]+):\s+(?P<body>.+)$")


def _classify(status_text: str) -> str:
    low = status_text.lower()
    if "✓" in status_text or "connected" in low:
        return AGENT_MCP_CONNECTED
    if "auth" in low:  # "Needs authentication"
        return AGENT_MCP_NEEDS_AUTH
    if "✗" in status_text or "fail" in low or "error" in low or "disconnected" in low:
        return AGENT_MCP_FAILED
    return AGENT_MCP_UNKNOWN


def parse_claude_mcp_list(output: str) -> dict[str, str]:
    """Parse ``claude mcp list`` stdout into ``{server_name: agent_status}``.

    Each server line looks like ``name: <command-or-url> - ✓ Connected`` (or
    ``✗ Failed to connect`` / ``Needs authentication``). The verdict is the
    segment after the final `` - `` on the line; everything before the first
    ``:`` is the (sanitized) server name. Lines without a `` - `` verdict and the
    ``Checking MCP server health...`` banner / blank lines are ignored.
    """
    result: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        match = _MCP_LIST_LINE.match(line)
        if not match:
            continue
        body = match.group("body")
        if " - " not in body:
            continue
        status_text = body.rsplit(" - ", 1)[1].strip()
        result[match.group("name")] = _classify(status_text)
    return result


def rollup_statuses(statuses: Iterable[str]) -> str | None:
    """Collapse many agents' verdicts for one server into a single headline.

    Returns ``None`` when no agent reported on the server (so callers can render
    "no agent data" distinctly from "all agents unknown").
    """
    seen = set(statuses)
    if not seen:
        return None
    for status in _ROLLUP_ORDER:
        if status in seen:
            return status
    return AGENT_MCP_UNKNOWN


async def collect_agent_mcp_health(
    agents: list,
    exec_list: Callable[[str], Awaitable[str | None]],
    server_names: Iterable[str],
    *,
    per_agent_timeout: float = 25.0,
) -> dict:
    """Run the agent-side check across ``agents`` and aggregate per server name.

    ``exec_list(container_id)`` returns the raw ``claude mcp list`` stdout for one
    agent, or ``None`` if the check could not run (agent unreachable, command
    failed). Such agents are skipped (fail-soft) — one broken agent never fails
    the whole report. Results are restricted to ``server_names`` (the registered
    external servers) so the agent's built-in stdio servers are ignored.

    Each ``agent`` must expose ``id``, ``name`` and ``container_id`` attributes.
    """
    wanted = set(server_names)
    per_server: dict[str, list[dict]] = {name: [] for name in wanted}

    async def _one(agent) -> str | None:
        try:
            return await asyncio.wait_for(exec_list(agent.container_id), per_agent_timeout)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — fail-soft per agent
            return None

    outputs = await asyncio.gather(*[_one(agent) for agent in agents])

    checked = 0
    for agent, output in zip(agents, outputs):
        if not output:
            continue
        checked += 1
        parsed = parse_claude_mcp_list(output)
        for name in wanted:
            status = parsed.get(name)
            if status is None:
                continue
            per_server[name].append(
                {"agent_id": agent.id, "agent_name": agent.name, "status": status}
            )

    servers: dict[str, dict] = {}
    for name, entries in per_server.items():
        statuses = [entry["status"] for entry in entries]
        servers[name] = {
            "connected": statuses.count(AGENT_MCP_CONNECTED),
            "failed": statuses.count(AGENT_MCP_FAILED),
            "needs_auth": statuses.count(AGENT_MCP_NEEDS_AUTH),
            "unknown": statuses.count(AGENT_MCP_UNKNOWN),
            "agent_status": rollup_statuses(statuses),
            "agents": entries,
        }

    return {"agents_checked": checked, "servers": servers}
