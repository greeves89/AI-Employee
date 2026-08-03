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
from app.models.audit_log import AuditEventType, AuditLog
from app.models.mcp_server import McpServer

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _sanitize_mcp_name(name: str) -> str:
    """Sanitize MCP server name: only letters, numbers, hyphens, underscores."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")


class McpServerCreate(BaseModel):
    name: str
    url: str
    bearer_token: str | None = None  # plaintext on input; stored Fernet-encrypted
    # Custom auth headers {name: value} for servers expecting a non-Bearer key.
    headers: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        return _sanitize_mcp_name(v)


class McpServerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    bearer_token: str | None = None  # "" clears the token; None leaves it unchanged
    headers: dict[str, str] | None = None  # {} clears; None leaves unchanged


class McpToolCall(BaseModel):
    name: str
    arguments: dict = {}


async def _write_audit(
    db: AsyncSession, event_type: AuditEventType, command: str,
    outcome: str, user_id: str, meta: dict | None = None,
) -> None:
    """Persist one MCP audit row. Never raises — auditing must not break the request."""
    try:
        db.add(AuditLog(
            agent_id="admin",
            event_type=event_type,
            command=command,
            outcome=outcome,
            user_id=user_id,
            meta=meta,
        ))
        await db.commit()
    except Exception:
        await db.rollback()


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


def _build_headers(
    bearer_token: str | None, extra_headers: dict[str, str] | None,
) -> dict[str, str]:
    """Build the Streamable-HTTP request headers, merging Bearer + custom auth."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if extra_headers:
        headers.update({str(k): str(v) for k, v in extra_headers.items() if k})
    return headers


async def _initialize_session(
    client: httpx.AsyncClient, url: str, headers: dict[str, str],
) -> dict[str, str]:
    """Run the MCP ``initialize`` handshake + ``initialized`` notification.

    Returns the headers to use for subsequent requests (carrying the
    ``mcp-session-id`` for stateful servers). Raises ``HTTPException(400)`` with
    the real cause in ``detail`` if the server rejects the handshake — a 502
    would be swallowed by a fronting Cloudflare tunnel, hiding the reason.
    """
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
            status_code=400,
            detail=f"MCP server returned {init_resp.status_code} on initialize "
                   "(check URL and auth token/headers)",
        )

    init_data = _parse_jsonrpc_response(init_resp)
    if not init_data or "result" not in init_data:
        raise HTTPException(
            status_code=400,
            detail="MCP server returned an invalid initialize response",
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
    return tool_headers


async def _discover_tools(
    url: str,
    bearer_token: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> list[dict]:
    """Connect to an MCP server via Streamable HTTP and list its tools.

    Handles both application/json and text/event-stream (SSE) responses,
    as servers like n8n respond with SSE format. An optional Bearer token is
    sent as ``Authorization: Bearer <token>``; ``extra_headers`` (e.g. an
    ``x-api-key``) are merged on top so non-Bearer servers can authenticate.
    """
    headers = _build_headers(bearer_token, extra_headers)

    async with httpx.AsyncClient(timeout=15.0) as client:
        tool_headers = await _initialize_session(client, url, headers)

        # List tools
        tools_resp = await client.post(url, headers=tool_headers, json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })

        if tools_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
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


async def _call_tool(
    url: str,
    tool_name: str,
    arguments: dict,
    bearer_token: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """Invoke a single tool (``tools/call``) and return the raw JSON-RPC object.

    Reuses the same handshake as :func:`_discover_tools`. The returned dict is the
    server's response verbatim — including a JSON-RPC ``error`` member if the tool
    itself failed — so the operator sees exactly what the server said. Transport
    failures raise ``HTTPException(400)`` with the real cause in ``detail``.
    """
    headers = _build_headers(bearer_token, extra_headers)

    async with httpx.AsyncClient(timeout=30.0) as client:
        tool_headers = await _initialize_session(client, url, headers)

        call_resp = await client.post(url, headers=tool_headers, json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        })

        if call_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"MCP server returned {call_resp.status_code} on tools/call",
            )

        data = _parse_jsonrpc_response(call_resp)
        if isinstance(data, list):
            # Batch response — pick the entry matching our request id
            for item in data:
                if isinstance(item, dict) and item.get("id") == 3:
                    return item
        if isinstance(data, dict):
            return data
        raise HTTPException(
            status_code=400,
            detail="MCP server returned an unparseable tools/call response",
        )


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
                "has_auth": bool(s.auth_token_encrypted),
                "has_headers": bool(s.headers_encrypted),
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
        tools = await _discover_tools(body.url, body.bearer_token, body.headers)
    except HTTPException as e:
        await _write_audit(db, AuditEventType.MCP_DISCOVERY_FAILED, f"add:{body.name}",
                           "failure", str(user.id), {"url": body.url, "detail": str(e.detail)})
        raise
    except Exception as e:
        await _write_audit(db, AuditEventType.MCP_DISCOVERY_FAILED, f"add:{body.name}",
                           "failure", str(user.id), {"url": body.url, "detail": str(e)})
        raise HTTPException(status_code=400, detail=f"Could not connect to MCP server: {e}")

    from app.core.encryption import encrypt_token
    server = McpServer(
        name=body.name, url=body.url, tools=tools, enabled=True,
        auth_token_encrypted=encrypt_token(body.bearer_token) if body.bearer_token else None,
        headers_encrypted=encrypt_token(json_mod.dumps(body.headers)) if body.headers else None,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": tools,
        "enabled": server.enabled,
        "has_auth": bool(server.auth_token_encrypted),
        "has_headers": bool(server.headers_encrypted),
    }


@router.post("/{server_id}/refresh")
async def refresh_mcp_tools(server_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Re-discover tools from an MCP server."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    from app.core.encryption import decrypt_token
    token = decrypt_token(server.auth_token_encrypted) if server.auth_token_encrypted else None
    extra = json_mod.loads(decrypt_token(server.headers_encrypted)) if server.headers_encrypted else None
    try:
        tools = await _discover_tools(server.url, token, extra)
    except HTTPException as e:
        await _write_audit(db, AuditEventType.MCP_DISCOVERY_FAILED, f"refresh:{server.name}",
                           "failure", str(user.id), {"server_id": server.id, "detail": str(e.detail)})
        raise
    except Exception as e:
        await _write_audit(db, AuditEventType.MCP_DISCOVERY_FAILED, f"refresh:{server.name}",
                           "failure", str(user.id), {"server_id": server.id, "detail": str(e)})
        raise HTTPException(status_code=400, detail=f"Could not connect: {e}")

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
        "has_auth": bool(server.auth_token_encrypted),
        "has_headers": bool(server.headers_encrypted),
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
    if body.bearer_token is not None:
        from app.core.encryption import encrypt_token
        server.auth_token_encrypted = encrypt_token(body.bearer_token) if body.bearer_token.strip() else None
    if body.headers is not None:
        from app.core.encryption import encrypt_token
        server.headers_encrypted = encrypt_token(json_mod.dumps(body.headers)) if body.headers else None

    await db.commit()
    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": server.tools or [],
        "enabled": server.enabled,
        "has_auth": bool(server.auth_token_encrypted),
        "has_headers": bool(server.headers_encrypted),
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
        # Pass the submitted bearer token AND custom headers so a probe against a
        # protected server actually authenticates (previously both were dropped →
        # a correctly-configured server always failed the connection test).
        tools = await _discover_tools(body.url, body.bearer_token, body.headers)
    except HTTPException as e:
        await _write_audit(db, AuditEventType.MCP_DISCOVERY_FAILED, f"probe:{body.name}",
                           "failure", str(user.id), {"url": body.url, "detail": str(e.detail)})
        raise
    except Exception as e:
        await _write_audit(db, AuditEventType.MCP_DISCOVERY_FAILED, f"probe:{body.name}",
                           "failure", str(user.id), {"url": body.url, "detail": str(e)})
        raise HTTPException(status_code=400, detail=f"Could not connect to MCP server: {e}")

    return {"url": body.url, "tools": tools, "tool_count": len(tools)}


@router.post("/{server_id}/call")
async def call_mcp_tool(
    server_id: int, body: McpToolCall, user=Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Invoke a single tool on a saved MCP server by hand and record the attempt.

    Diagnostic plumbing for operators (#414): a successful ``tools/call`` against a
    real server settles in one step whether URL + credential + connection state all
    line up, and a persisted audit row turns "it broke yesterday" into something
    answerable. Admin-only, like every other route here. The raw JSON-RPC result is
    returned verbatim (including a JSON-RPC ``error`` member if the tool failed).
    """
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    from app.core.encryption import decrypt_token
    token = decrypt_token(server.auth_token_encrypted) if server.auth_token_encrypted else None
    extra = json_mod.loads(decrypt_token(server.headers_encrypted)) if server.headers_encrypted else None

    try:
        rpc = await _call_tool(server.url, body.name, body.arguments, token, extra)
    except HTTPException as e:
        # Transport / handshake failure — the call never ran on the server.
        await _write_audit(db, AuditEventType.MCP_TOOL_CALL_FAILED, f"{server.name}:{body.name}",
                           "failure", str(user.id), {"server_id": server.id, "tool": body.name,
                                                      "detail": str(e.detail)})
        raise
    except Exception as e:
        await _write_audit(db, AuditEventType.MCP_TOOL_CALL_FAILED, f"{server.name}:{body.name}",
                           "failure", str(user.id), {"server_id": server.id, "tool": body.name,
                                                      "detail": str(e)})
        raise HTTPException(status_code=400, detail=f"Could not call tool: {e}")

    # A well-formed response can still carry a JSON-RPC error (the tool itself failed).
    is_error = isinstance(rpc, dict) and "error" in rpc
    # Do NOT persist arguments (may contain secrets) — only server + tool + outcome.
    await _write_audit(db, AuditEventType.MCP_TOOL_CALLED, f"{server.name}:{body.name}",
                       "failure" if is_error else "success", str(user.id),
                       {"server_id": server.id, "tool": body.name})

    return {"server_id": server.id, "tool": body.name, "result": rpc, "is_error": is_error}
