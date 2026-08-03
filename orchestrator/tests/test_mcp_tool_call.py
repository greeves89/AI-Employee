"""Tests for the manual MCP tool-call endpoint + audit trail (#414)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import mcp_servers
from app.api.mcp_servers import _call_tool, call_mcp_tool
from app.models.audit_log import AuditEventType


def _httpx_ctx(responses):
    """Build a mocked httpx.AsyncClient context manager whose .post() returns the
    given responses in order (each a MagicMock with status_code/headers/json)."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=responses)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {"content-type": "application/json"}
    r.json = MagicMock(return_value=body)
    r.text = ""
    return r


def test_audit_event_types_exist():
    assert AuditEventType.MCP_TOOL_CALLED.value == "mcp_tool_called"
    assert AuditEventType.MCP_TOOL_CALL_FAILED.value == "mcp_tool_call_failed"
    assert AuditEventType.MCP_DISCOVERY_FAILED.value == "mcp_discovery_failed"


@pytest.mark.asyncio
async def test_call_tool_returns_raw_rpc_result():
    init = _resp(body={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
    notified = _resp(body={})
    call = _resp(body={"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "hi"}]}})
    ctx = _httpx_ctx([init, notified, call])

    with patch("app.api.mcp_servers.httpx.AsyncClient", return_value=ctx):
        out = await _call_tool("http://x/mcp", "greet", {"name": "Ada"})

    assert out["result"]["content"][0]["text"] == "hi"


@pytest.mark.asyncio
async def test_call_tool_passes_through_jsonrpc_error():
    init = _resp(body={"jsonrpc": "2.0", "id": 1, "result": {}})
    notified = _resp(body={})
    call = _resp(body={"jsonrpc": "2.0", "id": 3, "error": {"code": -32602, "message": "bad args"}})
    ctx = _httpx_ctx([init, notified, call])

    with patch("app.api.mcp_servers.httpx.AsyncClient", return_value=ctx):
        out = await _call_tool("http://x/mcp", "greet", {})

    assert out["error"]["message"] == "bad args"


def _db_with_server(server):
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=server)
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    return db


def _server():
    s = MagicMock()
    s.id = 7
    s.name = "weather"
    s.url = "http://x/mcp"
    s.auth_token_encrypted = None
    s.headers_encrypted = None
    return s


def _added_audit(db):
    for c in db.add.call_args_list:
        arg = c.args[0]
        if arg.__class__.__name__ == "AuditLog":
            return arg
    return None


@pytest.mark.asyncio
async def test_route_success_audits_mcp_tool_called():
    db = _db_with_server(_server())
    user = MagicMock(id="admin-1")
    body = mcp_servers.McpToolCall(name="forecast", arguments={"city": "Berlin"})

    with patch("app.api.mcp_servers._call_tool", AsyncMock(return_value={"result": {"ok": True}})):
        resp = await call_mcp_tool(7, body, user=user, db=db)

    assert resp["is_error"] is False
    audit = _added_audit(db)
    assert audit is not None
    assert audit.event_type == AuditEventType.MCP_TOOL_CALLED
    assert audit.outcome == "success"
    # arguments must NOT be persisted (may contain secrets)
    assert "arguments" not in (audit.meta or {})
    assert audit.meta["tool"] == "forecast"


@pytest.mark.asyncio
async def test_route_marks_jsonrpc_error_as_failure():
    db = _db_with_server(_server())
    user = MagicMock(id="admin-1")
    body = mcp_servers.McpToolCall(name="forecast", arguments={})

    with patch("app.api.mcp_servers._call_tool", AsyncMock(return_value={"error": {"message": "nope"}})):
        resp = await call_mcp_tool(7, body, user=user, db=db)

    assert resp["is_error"] is True
    audit = _added_audit(db)
    assert audit.event_type == AuditEventType.MCP_TOOL_CALLED
    assert audit.outcome == "failure"


@pytest.mark.asyncio
async def test_route_transport_failure_audits_and_reraises():
    db = _db_with_server(_server())
    user = MagicMock(id="admin-1")
    body = mcp_servers.McpToolCall(name="forecast", arguments={})

    with patch("app.api.mcp_servers._call_tool", AsyncMock(side_effect=HTTPException(status_code=400, detail="401"))):
        with pytest.raises(HTTPException):
            await call_mcp_tool(7, body, user=user, db=db)

    audit = _added_audit(db)
    assert audit is not None
    assert audit.event_type == AuditEventType.MCP_TOOL_CALL_FAILED
    assert audit.outcome == "failure"


@pytest.mark.asyncio
async def test_route_404_when_server_missing():
    db = _db_with_server(None)
    user = MagicMock(id="admin-1")
    body = mcp_servers.McpToolCall(name="forecast", arguments={})

    with pytest.raises(HTTPException) as ei:
        await call_mcp_tool(999, body, user=user, db=db)
    assert ei.value.status_code == 404
