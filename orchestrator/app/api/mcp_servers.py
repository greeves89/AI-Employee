"""API endpoints for managing external MCP servers."""

import asyncio
import json as json_mod
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import urlparse, urlunparse

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

MCP_HEALTH_OK = "ok"
MCP_HEALTH_AUTH_FAILED = "auth_failed"
MCP_HEALTH_UNREACHABLE = "unreachable"
MCP_HEALTH_PROTOCOL_ERROR = "protocol_error"
MCP_HEALTH_STATUSES = {
    MCP_HEALTH_OK,
    MCP_HEALTH_AUTH_FAILED,
    MCP_HEALTH_UNREACHABLE,
    MCP_HEALTH_PROTOCOL_ERROR,
}


@dataclass
class McpDiscoveryError(Exception):
    status: str
    message: str

    def __str__(self) -> str:
        return self.message


def _sanitize_mcp_name(name: str) -> str:
    """Sanitize MCP server name: only letters, numbers, hyphens, underscores."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")


def _mcp_allow_private() -> bool:
    """Whether MCP server URLs may point at private/loopback hosts.

    Off by default (secure): the URL is operator-supplied infrastructure, but a
    typo or a compromised admin session must not turn this into an SSRF pivot to
    the DB, Redis or the cloud metadata endpoint. Deployments whose MCP servers
    genuinely live on the internal docker network set MCP_ALLOW_PRIVATE_URLS=true.
    """
    return os.getenv("MCP_ALLOW_PRIVATE_URLS", "").strip().lower() in ("1", "true", "yes")


async def _assert_mcp_url_allowed(url: str) -> None:
    """SSRF guard for a manual ``tools/call`` invocation. Raises HTTPException(400) if blocked.

    Resolves the hostname and checks every returned address (so a name that
    resolves to one public and one internal IP is still rejected), unlike
    :func:`_validate_mcp_url` which only catches IP-literal hosts. Link-local
    (incl. the 169.254.169.254 cloud-metadata endpoint), multicast, reserved and
    unspecified addresses are rejected unconditionally; private/loopback ranges
    are rejected unless ``MCP_ALLOW_PRIVATE_URLS`` is set.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="MCP server URL must be a valid http(s) URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"Cannot resolve MCP host: {parsed.hostname}") from e
    if not infos:
        raise HTTPException(status_code=400, detail=f"Cannot resolve MCP host: {parsed.hostname}")
    allow_private = _mcp_allow_private()
    for info in infos:
        ip = ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(
                status_code=400,
                detail=f"MCP host resolves to a forbidden address ({ip})",
            )
        if (ip.is_private or ip.is_loopback) and not allow_private:
            raise HTTPException(
                status_code=400,
                detail=f"MCP host resolves to a private address ({ip}); "
                       "set MCP_ALLOW_PRIVATE_URLS=true to allow internal MCP servers",
            )


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


def _audit_discovery_failure(db: AsyncSession, command: str, user_id: str, meta: dict) -> None:
    """Stage an MCP_DISCOVERY_FAILED row without committing.

    Used on the add/refresh/probe failure paths, which already have their own
    single commit right after (for refresh, that commit also persists the
    health-state fields set by ``_mark_health``). Staging here instead of
    committing separately keeps that call to exactly one commit.
    """
    db.add(AuditLog(
        agent_id="admin",
        event_type=AuditEventType.MCP_DISCOVERY_FAILED,
        command=command,
        outcome="failure",
        user_id=user_id,
        meta=meta,
    ))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _short_error(text: str | None) -> str | None:
    if not text:
        return None
    compact = " ".join(str(text).split())
    return compact[:252] + "..." if len(compact) > 255 else compact


def _status_from_http(status_code: int) -> str:
    return MCP_HEALTH_AUTH_FAILED if status_code == 401 else MCP_HEALTH_PROTOCOL_ERROR


def _response_error(resp: httpx.Response, phase: str) -> McpDiscoveryError:
    reason = resp.reason_phrase or "HTTP error"
    return McpDiscoveryError(
        _status_from_http(resp.status_code),
        _short_error(f"{resp.status_code} {reason} on {phase}") or "MCP server request failed",
    )


def _serialize_mcp_server(server: McpServer) -> dict:
    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": server.tools or [],
        "enabled": server.enabled,
        "has_auth": bool(server.auth_token_encrypted),
        "has_headers": bool(server.headers_encrypted),
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "last_checked_at": server.last_checked_at.isoformat() if server.last_checked_at else None,
        "last_status": server.last_status,
        "last_error": server.last_error,
    }


def _mark_health(server: McpServer, status: str, error: str | None = None) -> None:
    if status not in MCP_HEALTH_STATUSES:
        status = MCP_HEALTH_PROTOCOL_ERROR
    server.last_checked_at = _now_utc()
    server.last_status = status
    server.last_error = _short_error(error)


def _validate_mcp_url(url: str) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL") from exc
    if port is not None and not (1 <= port <= 65535):
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL")

    host = parsed.hostname.strip("[]").lower()
    if host in {"localhost", "metadata.google.internal"}:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "MCP server URL host is not allowed")
    try:
        ip = ip_address(host)
    except ValueError:
        pass
    else:
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_private:
            raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "MCP server URL host is not allowed")

    return urlunparse(parsed._replace(fragment=""))


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
    """Run the MCP ``initialize`` handshake + ``initialized`` notification for a
    manual ``tools/call`` (see :func:`_call_tool`).

    Returns the headers to use for subsequent requests (carrying the
    ``mcp-session-id`` for stateful servers). Raises ``HTTPException(400)`` with
    the real cause in ``detail`` if the server rejects the handshake — a 502
    would be swallowed by a fronting Cloudflare tunnel, hiding the reason.

    :func:`_discover_tools` runs its own copy of this handshake inline because it
    needs to classify failures into health states (``McpDiscoveryError``) rather
    than raise a flat ``HTTPException``; a manual tool call is not tracked in
    server health, so the simpler exception here is sufficient.
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
    safe_url = _validate_mcp_url(url)
    headers = _build_headers(bearer_token, extra_headers)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Initialize
        try:
            init_resp = await client.post(safe_url, headers=headers, json={  # codeql[py/full-ssrf]: URL is validated by _validate_mcp_url above.
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ai-employee-orchestrator", "version": "1.0.0"},
                },
            })
        except httpx.RequestError as exc:
            raise McpDiscoveryError(
                MCP_HEALTH_UNREACHABLE,
                "Connection failed during initialize",
            ) from exc

        if init_resp.status_code != 200:
            raise _response_error(init_resp, "initialize")

        init_data = _parse_jsonrpc_response(init_resp)
        if not init_data or "result" not in init_data:
            raise McpDiscoveryError(
                MCP_HEALTH_PROTOCOL_ERROR,
                "Invalid initialize response",
            )

        # Extract session ID from response header if present (for stateful servers)
        session_id = init_resp.headers.get("mcp-session-id")
        tool_headers = {**headers}
        if session_id:
            tool_headers["mcp-session-id"] = session_id

        # Send initialized notification
        try:
            await client.post(safe_url, headers=tool_headers, json={  # codeql[py/full-ssrf]: URL is validated by _validate_mcp_url above.
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })
        except httpx.RequestError as exc:
            raise McpDiscoveryError(
                MCP_HEALTH_UNREACHABLE,
                "Connection failed during initialized notification",
            ) from exc

        # Step 2: List tools
        try:
            tools_resp = await client.post(safe_url, headers=tool_headers, json={  # codeql[py/full-ssrf]: URL is validated by _validate_mcp_url above.
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })
        except httpx.RequestError as exc:
            raise McpDiscoveryError(
                MCP_HEALTH_UNREACHABLE,
                "Connection failed during tools/list",
            ) from exc

        if tools_resp.status_code != 200:
            raise _response_error(tools_resp, "tools/list")

        data = _parse_jsonrpc_response(tools_resp)

        if isinstance(data, dict) and "result" in data:
            return data["result"].get("tools", [])
        elif isinstance(data, list):
            # Batch response
            for item in data:
                if isinstance(item, dict) and item.get("id") == 2 and "result" in item:
                    return item["result"].get("tools", [])

        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid tools/list response")


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
    await _assert_mcp_url_allowed(url)
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
            _serialize_mcp_server(s)
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
    except McpDiscoveryError as e:
        _audit_discovery_failure(db, f"add:{body.name}", str(user.id),
                                  {"url": body.url, "detail": e.message})
        await db.commit()
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        _audit_discovery_failure(db, f"add:{body.name}", str(user.id),
                                  {"url": body.url, "detail": _short_error(str(e))})
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect to MCP server: {_short_error(str(e))}")

    from app.core.encryption import encrypt_token
    server = McpServer(
        name=body.name, url=body.url, tools=tools, enabled=True,
        auth_token_encrypted=encrypt_token(body.bearer_token) if body.bearer_token else None,
        headers_encrypted=encrypt_token(json_mod.dumps(body.headers)) if body.headers else None,
    )
    _mark_health(server, MCP_HEALTH_OK)
    db.add(server)
    await db.commit()
    await db.refresh(server)

    return _serialize_mcp_server(server)


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
    except McpDiscoveryError as e:
        _mark_health(server, e.status, e.message)
        _audit_discovery_failure(db, f"refresh:{server.name}", str(user.id),
                                  {"server_id": server.id, "detail": e.message})
        await db.commit()
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        _mark_health(server, MCP_HEALTH_PROTOCOL_ERROR, "Unexpected discovery error")
        _audit_discovery_failure(db, f"refresh:{server.name}", str(user.id),
                                  {"server_id": server.id, "detail": _short_error(str(e))})
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect: {_short_error(str(e))}")

    server.tools = tools
    _mark_health(server, MCP_HEALTH_OK)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(server, "tools")
    await db.commit()

    return _serialize_mcp_server(server)


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
    return _serialize_mcp_server(server)


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
    except McpDiscoveryError as e:
        _audit_discovery_failure(db, f"probe:{body.name}", str(user.id),
                                  {"url": body.url, "detail": e.message})
        await db.commit()
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        _audit_discovery_failure(db, f"probe:{body.name}", str(user.id),
                                  {"url": body.url, "detail": _short_error(str(e))})
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect to MCP server: {_short_error(str(e))}")

    return {
        "url": body.url,
        "tools": tools,
        "tool_count": len(tools),
        "last_checked_at": _now_utc().isoformat(),
        "last_status": MCP_HEALTH_OK,
        "last_error": None,
    }


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
