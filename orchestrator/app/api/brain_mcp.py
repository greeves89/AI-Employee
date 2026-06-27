"""MCP HTTP Server per Second Brain.

Exposes each Second Brain vault as a proper MCP server endpoint that n8n,
Cursor and other MCP clients can connect to via the 2025-06-18 Streamable HTTP
transport — the same pattern as the per-agent server (``mcp_agent.py``).

Endpoint: POST /api/v1/mcp/brains/{slug}
Auth:      Authorization: Bearer <brain MCP token>   (generated in the UI)

Supported MCP methods:
  initialize                → server capabilities + brain info
  notifications/initialized → ack (no-op)
  ping                      → pong
  tools/list                → brain_search, brain_read, brain_list
  tools/call                → execute a tool against the vault
"""
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Reuse the exact JSON-RPC envelope helpers + protocol version from the agent
# MCP server — one MCP implementation, no parallel copy.
from app.api.mcp_agent import (
    MCP_PROTOCOL_VERSION,
    _mcp_error,
    _mcp_result,
    _tool_result,
)
from app.core import vault
from app.core.encryption import decrypt_token
from app.db.session import get_db
from app.models.second_brain import SecondBrain

router = APIRouter(prefix="/mcp", tags=["mcp-brain"])

BRAIN_TOOLS = [
    {
        "name": "brain_search",
        "description": (
            "Search this shared knowledge vault (Second Brain) for relevant "
            "Markdown notes. Use BEFORE answering support/how-to/troubleshooting "
            "questions. Returns matching files with snippet lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to search for (error codes, device names, topics).",
                },
                "limit": {
                    "type": "string",
                    "description": "Max number of files to return (default 10, max 50).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "brain_read",
        "description": "Read the full Markdown content of one vault file by its path (as returned by brain_search or brain_list).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative file path, e.g. 'Drucker/x17137.md'.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "brain_list",
        "description": "List all files in this vault so you can pick one to read.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def _auth_brain(slug: str, request: Request, db: AsyncSession) -> SecondBrain:
    """Verify the brain exists, has MCP enabled, and the Bearer token matches."""
    brain = (
        await db.execute(select(SecondBrain).where(SecondBrain.slug == slug))
    ).scalar_one_or_none()
    if not brain:
        raise HTTPException(status_code=404, detail="Brain not found")
    if not brain.is_active or not brain.mcp_enabled or not brain.mcp_token_encrypted:
        raise HTTPException(
            status_code=403,
            detail="MCP access is not enabled for this Second Brain.",
        )
    try:
        expected = decrypt_token(brain.mcp_token_encrypted)
    except ValueError:
        raise HTTPException(status_code=500, detail="Token decryption failed (ENCRYPTION_KEY changed?)")

    auth_header = request.headers.get("Authorization", "")
    provided = auth_header.removeprefix("Bearer ").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")
    return brain


@router.post("/brains/{slug}")
async def mcp_brain_endpoint(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """MCP Streamable HTTP endpoint — handles all JSON-RPC requests from MCP clients."""
    brain = await _auth_brain(slug, request, db)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_mcp_error(None, -32700, "Parse error: invalid JSON"), status_code=200)

    if isinstance(body, list):
        responses = []
        for req in body:
            resp = _handle_rpc(req, brain)
            if resp is not None:
                responses.append(resp)
        return JSONResponse(responses)

    resp = _handle_rpc(body, brain)
    if resp is None:
        return JSONResponse(None, status_code=202)  # notification — no body
    return JSONResponse(resp)


def _handle_rpc(req: dict, brain: SecondBrain):
    """Dispatch a single JSON-RPC request (or None for notifications)."""
    rpc_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    if rpc_id is None:  # notification (e.g. notifications/initialized)
        return None

    if method == "initialize":
        return _mcp_result(rpc_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": f"ai-employee-brain/{brain.slug}", "version": "1.0.0"},
        })

    if method == "ping":
        return _mcp_result(rpc_id, {})

    if method == "tools/list":
        return _mcp_result(rpc_id, {"tools": BRAIN_TOOLS})

    if method == "tools/call":
        return _mcp_result(rpc_id, _call_tool(params.get("name", ""), params.get("arguments", {}), brain))

    return _mcp_error(rpc_id, -32601, f"Method not found: {method}")


def _call_tool(name: str, args: dict, brain: SecondBrain) -> dict:
    """Execute a vault tool and return an MCP tool result."""
    if name == "brain_search":
        query = (args.get("query") or "").strip()
        if not query:
            return _tool_result("Error: 'query' is required", is_error=True)
        try:
            limit = min(int(float(str(args.get("limit", 10)))), 50)
        except (ValueError, TypeError):
            limit = 10
        hits = vault.search(brain.host_path, query, limit)
        if not hits:
            return _tool_result(f"No matches for '{query}' in {brain.name}.")
        lines = [f"{len(hits)} match(es) in {brain.name}:"]
        for h in hits:
            lines.append(f"\n## {h['path']}")
            for s in h["snippets"]:
                lines.append(f"  · {s}")
        return _tool_result("\n".join(lines))

    if name == "brain_read":
        path = (args.get("path") or "").strip()
        if not path:
            return _tool_result("Error: 'path' is required", is_error=True)
        try:
            content = vault.read_file(brain.host_path, path)
        except FileNotFoundError:
            return _tool_result(f"File not found: {path}", is_error=True)
        except ValueError as e:
            return _tool_result(f"Error: {e}", is_error=True)
        return _tool_result(content)

    if name == "brain_list":
        entries = vault.list_entries(brain.host_path)
        files = [e["path"] for e in entries if e["type"] == "file"]
        if not files:
            return _tool_result(f"{brain.name} is empty.")
        return _tool_result(
            f"{len(files)} file(s) in {brain.name}:\n" + "\n".join(f"  {p}" for p in files)
        )

    return _tool_result(f"Unknown tool: {name}", is_error=True)
