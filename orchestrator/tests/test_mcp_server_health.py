from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import mcp_servers
from app.models.mcp_server import McpServer


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ListResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


class _FakeSession:
    def __init__(self, server):
        self.server = server
        self.commits = 0
        self.added = []

    async def execute(self, _stmt):
        return _ScalarResult(self.server)

    async def commit(self):
        self.commits += 1

    def add(self, obj):
        self.added.append(obj)


class _FakeListSession:
    def __init__(self, servers):
        self.servers = servers

    async def execute(self, _stmt):
        return _ListResult(self.servers)


def test_mcp_server_serializer_exposes_health_fields():
    checked_at = datetime(2026, 8, 3, 10, 30, tzinfo=timezone.utc)
    server = McpServer(
        id=7,
        name="composio",
        url="https://mcp.example.test",
        tools=[{"name": "search"}],
        enabled=True,
        last_checked_at=checked_at,
        last_status="auth_failed",
        last_error="401 Unauthorized on tools/list",
    )

    payload = mcp_servers._serialize_mcp_server(server)

    assert payload["last_checked_at"] == checked_at.isoformat()
    assert payload["last_status"] == "auth_failed"
    assert payload["last_error"] == "401 Unauthorized on tools/list"
    assert payload["tools"] == [{"name": "search"}]


def test_validate_mcp_url_rejects_unsafe_literal_hosts():
    with pytest.raises(mcp_servers.McpDiscoveryError):
        mcp_servers._validate_mcp_url("http://127.0.0.1:8000/mcp")
    with pytest.raises(mcp_servers.McpDiscoveryError):
        mcp_servers._validate_mcp_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(mcp_servers.McpDiscoveryError):
        mcp_servers._validate_mcp_url("ftp://mcp.example.test")
    with pytest.raises(mcp_servers.McpDiscoveryError):
        mcp_servers._validate_mcp_url("https://token@mcp.example.test")


def test_validate_mcp_url_allows_https_mcp_hosts():
    assert (
        mcp_servers._validate_mcp_url(" https://mcp.example.test/path?tenant=abc ")
        == "https://mcp.example.test/path?tenant=abc"
    )


@pytest.mark.asyncio
async def test_list_mcp_servers_includes_health_fields():
    checked_at = datetime(2026, 8, 3, 10, 30, tzinfo=timezone.utc)
    server = McpServer(
        id=7,
        name="composio",
        url="https://mcp.example.test",
        tools=[{"name": "search"}],
        enabled=True,
        last_checked_at=checked_at,
        last_status="auth_failed",
        last_error="401 Unauthorized on tools/list",
    )
    db = _FakeListSession([server])

    payload = await mcp_servers.list_mcp_servers(user=SimpleNamespace(id="user"), db=db)

    assert payload["servers"][0]["last_checked_at"] == checked_at.isoformat()
    assert payload["servers"][0]["last_status"] == "auth_failed"
    assert payload["servers"][0]["last_error"] == "401 Unauthorized on tools/list"


@pytest.mark.asyncio
async def test_refresh_persists_ok_health_state(monkeypatch):
    server = McpServer(id=1, name="ok", url="https://mcp.example.test", tools=[], enabled=True)
    db = _FakeSession(server)

    async def fake_discover(_url, _token, _headers, **_kw):
        return [{"name": "ping"}]

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)

    payload = await mcp_servers.refresh_mcp_tools(1, user=SimpleNamespace(id="admin"), db=db)

    assert payload["last_status"] == "ok"
    assert payload["last_error"] is None
    assert payload["last_checked_at"] is not None
    assert payload["tools"] == [{"name": "ping"}]
    assert server.last_status == "ok"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_refresh_persists_auth_failed_health_state(monkeypatch):
    server = McpServer(id=1, name="bad-auth", url="https://mcp.example.test", tools=[], enabled=True)
    db = _FakeSession(server)

    async def fake_discover(_url, _token, _headers, **_kw):
        raise mcp_servers.McpDiscoveryError("auth_failed", "401 Unauthorized on initialize")

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)

    with pytest.raises(HTTPException) as exc:
        await mcp_servers.refresh_mcp_tools(1, user=SimpleNamespace(id="admin"), db=db)

    assert exc.value.status_code == 400
    assert exc.value.detail == "401 Unauthorized on initialize"
    assert server.last_status == "auth_failed"
    assert server.last_error == "401 Unauthorized on initialize"
    assert server.last_checked_at is not None
    assert db.commits == 1


def test_startup_ensure_mentions_mcp_health_columns():
    main_py = Path(__file__).resolve().parents[1] / "app" / "main.py"
    text = main_py.read_text()

    assert "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS last_checked_at timestamptz" in text
    assert "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS last_status varchar(32)" in text
    assert "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS last_error varchar(255)" in text
