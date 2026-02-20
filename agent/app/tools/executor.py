"""Tool executor - runs tools locally or via orchestrator API."""

import asyncio
import logging
import os
from pathlib import Path

from app.config import settings
from app.tools.definitions import ORCHESTRATOR_TOOL_NAMES

logger = logging.getLogger(__name__)

# Safety limit for bash output
MAX_OUTPUT_CHARS = 30000


class ToolExecutor:
    """Executes tool calls locally or routes them to the orchestrator API."""

    def __init__(self, workspace_dir: str | None = None):
        self.workspace_dir = workspace_dir or settings.workspace_dir
        self._api_client = None  # Lazy init
        self._mcp_client = None  # Lazy init for custom MCP servers

    def _get_api_client(self):
        """Lazy-initialize the orchestrator API client."""
        if self._api_client is None:
            from app.tools.api_client import OrchestratorAPIClient
            self._api_client = OrchestratorAPIClient()
        return self._api_client

    def _get_mcp_client(self):
        """Lazy-initialize the MCP HTTP client."""
        if self._mcp_client is None:
            from app.tools.mcp_client import MCPHTTPClient
            self._mcp_client = MCPHTTPClient()
        return self._mcp_client

    async def execute(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return the result as a string."""
        # Route orchestrator API tools to the API client
        if tool_name in ORCHESTRATOR_TOOL_NAMES:
            return await self._execute_api_tool(tool_name, tool_input)

        # Route custom MCP tools (prefixed with "mcp_")
        if tool_name.startswith("mcp_"):
            return await self._execute_mcp_tool(tool_name, tool_input)

        # Local tool execution
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            return await handler(tool_input)
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    async def _execute_api_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool via the orchestrator API client."""
        client = self._get_api_client()
        handler = getattr(client, tool_name, None)
        if not handler:
            return f"Error: API tool '{tool_name}' not implemented"
        try:
            return await handler(tool_input)
        except Exception as e:
            return f"Error executing API tool {tool_name}: {e}"

    async def _execute_mcp_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool via custom MCP HTTP server."""
        client = self._get_mcp_client()
        # Strip "mcp_" prefix and find the server + original tool name
        try:
            return await client.call_tool(tool_name, tool_input)
        except Exception as e:
            return f"Error executing MCP tool {tool_name}: {e}"

    def _resolve_path(self, path: str) -> str:
        """Resolve a path relative to workspace if not absolute."""
        if os.path.isabs(path):
            return path
        return os.path.join(self.workspace_dir, path)

    async def _tool_bash(self, params: dict) -> str:
        """Execute a bash command."""
        command = params.get("command", "")
        if not command:
            return "Error: No command provided"

        timeout = min(params.get("timeout", 30), 300)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            output = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            result = ""
            if output:
                result += output
            if err:
                result += f"\n[stderr]\n{err}" if result else err
            if not result:
                result = f"(exit code: {process.returncode})"

            if len(result) > MAX_OUTPUT_CHARS:
                result = result[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(result)} total chars)"

            return result

        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return f"Error: Command timed out after {timeout}s"

    async def _tool_read_file(self, params: dict) -> str:
        """Read a file's contents."""
        path = self._resolve_path(params.get("path", ""))
        max_lines = params.get("max_lines")

        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        if not os.path.isfile(path):
            return f"Error: Not a file: {path}"

        try:
            with open(path, "r", errors="replace") as f:
                if max_lines:
                    lines = []
                    for i, line in enumerate(f):
                        if i >= max_lines:
                            lines.append(f"... (truncated at {max_lines} lines)")
                            break
                        lines.append(line)
                    content = "".join(lines)
                else:
                    content = f.read()

            if len(content) > MAX_OUTPUT_CHARS:
                content = content[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(content)} total chars)"
            return content
        except Exception as e:
            return f"Error reading file: {e}"

    async def _tool_write_file(self, params: dict) -> str:
        """Write content to a file."""
        path = self._resolve_path(params.get("path", ""))
        content = params.get("content", "")

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} chars to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    async def _tool_list_files(self, params: dict) -> str:
        """List directory contents."""
        path = self._resolve_path(params.get("path", self.workspace_dir))
        recursive = params.get("recursive", False)

        if not os.path.exists(path):
            return f"Error: Path not found: {path}"
        if not os.path.isdir(path):
            return f"Error: Not a directory: {path}"

        try:
            entries = []
            if recursive:
                for root, dirs, files in os.walk(path):
                    # Limit depth to 3 levels
                    depth = root[len(path):].count(os.sep)
                    if depth >= 3:
                        dirs.clear()
                        continue
                    rel = os.path.relpath(root, path)
                    prefix = "" if rel == "." else rel + "/"
                    for d in sorted(dirs):
                        if d.startswith("."):
                            continue
                        entries.append(f"{prefix}{d}/")
                    for f in sorted(files):
                        if f.startswith("."):
                            continue
                        entries.append(f"{prefix}{f}")
            else:
                for entry in sorted(os.listdir(path)):
                    if entry.startswith("."):
                        continue
                    full = os.path.join(path, entry)
                    suffix = "/" if os.path.isdir(full) else ""
                    entries.append(f"{entry}{suffix}")

            if not entries:
                return "(empty directory)"
            return "\n".join(entries)
        except Exception as e:
            return f"Error listing directory: {e}"

    async def close(self) -> None:
        """Clean up resources."""
        if self._api_client:
            await self._api_client.close()
        if self._mcp_client:
            await self._mcp_client.close()
