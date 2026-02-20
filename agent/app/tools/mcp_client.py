"""MCP HTTP Client - connects to custom (user-configured) MCP servers.

Uses the MCP Streamable HTTP transport to discover and call tools on
external MCP servers. These are configured per-agent via CUSTOM_MCP_SERVERS
env var (JSON: {"server-name": "http://url"}).
"""

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MCPHTTPClient:
    """Client for custom MCP HTTP servers.

    Discovers tools from configured MCP servers and routes tool calls
    to the appropriate server. Tool names are prefixed with "mcp_{server}_{tool}"
    to avoid conflicts with built-in tools.
    """

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        # server_name -> url
        self._servers: dict[str, str] = {}
        # tool_name -> (server_name, original_tool_name)
        self._tool_registry: dict[str, tuple[str, str]] = {}
        # Discovered tool definitions (OpenAI format)
        self._tool_definitions: list[dict] = []
        self._initialized = False

    def _load_servers(self) -> None:
        """Load MCP server config from environment."""
        custom_mcp = os.environ.get("CUSTOM_MCP_SERVERS", "")
        if not custom_mcp:
            return
        try:
            servers = json.loads(custom_mcp)
            if isinstance(servers, dict):
                self._servers = servers
        except (json.JSONDecodeError, TypeError):
            logger.warning("Could not parse CUSTOM_MCP_SERVERS env var")

    async def discover_tools(self) -> list[dict]:
        """Discover tools from all configured MCP servers.

        Returns tool definitions in OpenAI function-calling format with
        prefixed names (mcp_{server}_{tool}).
        """
        if self._initialized:
            return self._tool_definitions

        self._load_servers()
        self._initialized = True

        if not self._servers:
            return []

        for server_name, server_url in self._servers.items():
            try:
                tools = await self._list_server_tools(server_name, server_url)
                self._tool_definitions.extend(tools)
                logger.info(f"Discovered {len(tools)} tools from MCP server '{server_name}'")
            except Exception as e:
                logger.warning(f"Could not discover tools from MCP server '{server_name}': {e}")

        return self._tool_definitions

    async def _list_server_tools(self, server_name: str, server_url: str) -> list[dict]:
        """List tools from a single MCP server via JSON-RPC."""
        url = server_url.rstrip("/")

        # MCP uses JSON-RPC 2.0
        # First: Initialize
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-employee-agent", "version": "1.0"},
            },
        }
        resp = await self._client.post(url, json=init_request)
        if resp.status_code != 200:
            logger.warning(f"MCP init failed for {server_name}: {resp.status_code}")
            return []

        # Then: List tools
        list_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        resp = await self._client.post(url, json=list_request)
        if resp.status_code != 200:
            logger.warning(f"MCP tools/list failed for {server_name}: {resp.status_code}")
            return []

        result = resp.json()
        mcp_tools = result.get("result", {}).get("tools", [])

        # Convert MCP tool format to OpenAI function-calling format
        openai_tools = []
        for tool in mcp_tools:
            original_name = tool.get("name", "")
            prefixed_name = f"mcp_{server_name}_{original_name}"

            # Register mapping for later tool calls
            self._tool_registry[prefixed_name] = (server_name, original_name)

            openai_tools.append({
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": f"[MCP: {server_name}] {tool.get('description', '')}",
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })

        return openai_tools

    async def call_tool(self, prefixed_name: str, tool_input: dict) -> str:
        """Call a tool on its MCP server.

        Args:
            prefixed_name: The prefixed tool name (mcp_{server}_{tool})
            tool_input: The tool input parameters
        """
        if prefixed_name not in self._tool_registry:
            return f"Error: Unknown MCP tool '{prefixed_name}'"

        server_name, original_name = self._tool_registry[prefixed_name]
        server_url = self._servers.get(server_name)
        if not server_url:
            return f"Error: MCP server '{server_name}' not found"

        url = server_url.rstrip("/")
        call_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": original_name,
                "arguments": tool_input,
            },
        }

        try:
            resp = await self._client.post(url, json=call_request)
            if resp.status_code != 200:
                return f"MCP tool error {resp.status_code}: {resp.text[:500]}"

            result = resp.json()

            # Check for JSON-RPC error
            if "error" in result:
                error = result["error"]
                return f"MCP error: {error.get('message', 'Unknown error')}"

            # Extract content from result
            tool_result = result.get("result", {})
            content_blocks = tool_result.get("content", [])

            # MCP returns content blocks, concatenate text blocks
            texts = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)

            return "\n".join(texts) if texts else json.dumps(tool_result)

        except httpx.ConnectError:
            return f"Error: Cannot connect to MCP server '{server_name}' at {server_url}"
        except Exception as e:
            return f"Error calling MCP tool: {e}"

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
