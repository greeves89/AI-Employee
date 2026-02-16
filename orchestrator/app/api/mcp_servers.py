"""API endpoints for managing external MCP servers."""

import json as json_mod
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.models.mcp_server import McpServer

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _sanitize_mcp_name(name: str) -> str:
    """Sanitize MCP server name: only letters, numbers, hyphens, underscores."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")


class McpServerCreate(BaseModel):
    name: str
    url: str

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        return _sanitize_mcp_name(v)


class McpServerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None


def _parse_jsonrpc_response(resp: httpx.Response) -> dict | None:
    """Parse a JSON-RPC response that may be JSON or SSE (text/event-stream)."""
    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        # Parse SSE: look for "data: " lines containing JSON
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    return json_mod.loads(line[6:])
                except json_mod.JSONDecodeError:
                    continue
        return None

    # application/json or other - try direct JSON parse
    try:
        return resp.json()
    except Exception:
        return None


async def _discover_tools(url: str) -> list[dict]:
    """Connect to an MCP server via Streamable HTTP and list its tools.

    Handles both application/json and text/event-stream (SSE) responses,
    as servers like n8n respond with SSE format.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Initialize
        init_resp = await client.post(url, headers=headers, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-employee-orchestrator", "version": "1.0.0"},
            },
        })

        if init_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"MCP server returned {init_resp.status_code} on initialize",
            )

        init_data = _parse_jsonrpc_response(init_resp)
        if not init_data or "result" not in init_data:
            raise HTTPException(
                status_code=502,
                detail="MCP server returned invalid initialize response",
            )

        # Extract session ID from response header if present (for stateful servers)
        session_id = init_resp.headers.get("mcp-session-id")
        tool_headers = {**headers}
        if session_id:
            tool_headers["mcp-session-id"] = session_id

        # Send initialized notification
        await client.post(url, headers=tool_headers, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

        # Step 2: List tools
        tools_resp = await client.post(url, headers=tool_headers, json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })

        if tools_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"MCP server returned {tools_resp.status_code} on tools/list",
            )

        data = _parse_jsonrpc_response(tools_resp)

        if isinstance(data, dict) and "result" in data:
            return data["result"].get("tools", [])
        elif isinstance(data, list):
            # Batch response
            for item in data:
                if isinstance(item, dict) and item.get("id") == 2 and "result" in item:
                    return item["result"].get("tools", [])

        return []


@router.get("")
async def list_mcp_servers(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """List all registered MCP servers."""
    result = await db.execute(select(McpServer).order_by(McpServer.created_at.desc()))
    servers = result.scalars().all()
    return {
        "servers": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "tools": s.tools or [],
                "enabled": s.enabled,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in servers
        ]
    }


@router.post("")
async def add_mcp_server(body: McpServerCreate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Register a new MCP server and discover its tools."""
    # Check for duplicate name
    existing = await db.execute(select(McpServer).where(McpServer.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"MCP server '{body.name}' already exists")

    # Discover tools
    try:
        tools = await _discover_tools(body.url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not connect to MCP server: {e}")

    server = McpServer(name=body.name, url=body.url, tools=tools, enabled=True)
    db.add(server)
    await db.commit()
    await db.refresh(server)

    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": tools,
        "enabled": server.enabled,
    }


@router.post("/{server_id}/refresh")
async def refresh_mcp_tools(server_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Re-discover tools from an MCP server."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    try:
        tools = await _discover_tools(server.url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not connect: {e}")

    server.tools = tools
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(server, "tools")
    await db.commit()

    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": tools,
        "enabled": server.enabled,
    }


@router.patch("/{server_id}")
async def update_mcp_server(
    server_id: int, body: McpServerUpdate, user=Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Update an MCP server's config."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    if body.name is not None:
        server.name = body.name
    if body.url is not None:
        server.url = body.url
    if body.enabled is not None:
        server.enabled = body.enabled

    await db.commit()
    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": server.tools or [],
        "enabled": server.enabled,
    }


@router.delete("/{server_id}")
async def delete_mcp_server(server_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Remove an MCP server."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    await db.delete(server)
    await db.commit()
    return {"deleted": True}


@router.post("/probe")
async def probe_mcp_server(body: McpServerCreate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Probe an MCP server URL without saving it. Returns discovered tools."""
    try:
        tools = await _discover_tools(body.url)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not connect to MCP server: {e}")

    return {"url": body.url, "tools": tools, "tool_count": len(tools)}
