"""Issue #465: adding a fresh OAuth-protected MCP server must succeed in a
needs_oauth state instead of aborting on the 401 that OAuth is meant to solve.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import mcp_servers
from app.models.mcp_server import McpServer


def _created_servers(db):
    return [o for o in db.added if isinstance(o, McpServer)]


class _NoDuplicateResult:
    def scalar_one_or_none(self):
        return None


class _FakeAddSession:
    """Session whose duplicate-name lookup finds nothing, so creation proceeds."""

    def __init__(self):
        self.commits = 0
        self.added = []

    async def execute(self, _stmt):
        return _NoDuplicateResult()

    async def commit(self):
        self.commits += 1

    def add(self, obj):
        self.added.append(obj)

    async def refresh(self, _obj):
        return None


def _body(**kw):
    defaults = dict(name="proxy-mcp", url="https://mcp.example.test/mcp",
                    bearer_token=None, headers=None, allow_private_host=False)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_add_oauth_protected_server_is_created_in_needs_oauth_state(monkeypatch):
    async def fake_discover(_url, _token, _headers, **_kw):
        raise mcp_servers.McpDiscoveryError("auth_failed", "401 Unauthorized on initialize")

    async def fake_advertises(_url):
        return True

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)
    monkeypatch.setattr(mcp_servers, "_advertises_oauth", fake_advertises)

    db = _FakeAddSession()
    payload = await mcp_servers.add_mcp_server(_body(), user=SimpleNamespace(id="admin"), db=db)

    assert payload["needs_oauth"] is True
    assert payload["oauth_enabled"] is True
    assert payload["last_status"] == "needs_oauth"
    assert payload["tools"] == []
    # The server row was actually persisted so the Connect flow can reach it.
    assert len(db.added) == 1
    assert db.commits == 1
    created = db.added[0]
    assert created.oauth_enabled is True
    # OAuth is the auth mechanism here — no irrelevant static creds get stored.
    assert created.auth_token_encrypted is None
    assert created.headers_encrypted is None


@pytest.mark.asyncio
async def test_add_rejected_static_token_without_oauth_still_aborts(monkeypatch):
    """A 401 with NO OAuth challenge is a real auth failure and must not create a row."""
    async def fake_discover(_url, _token, _headers, **_kw):
        raise mcp_servers.McpDiscoveryError("auth_failed", "401 Unauthorized on initialize")

    async def fake_advertises(_url):
        return False

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)
    monkeypatch.setattr(mcp_servers, "_advertises_oauth", fake_advertises)

    db = _FakeAddSession()
    with pytest.raises(HTTPException) as exc:
        await mcp_servers.add_mcp_server(_body(bearer_token="wrong"),
                                         user=SimpleNamespace(id="admin"), db=db)

    assert exc.value.status_code == 400
    assert exc.value.detail == "401 Unauthorized on initialize"
    assert _created_servers(db) == []


@pytest.mark.asyncio
async def test_add_unreachable_does_not_probe_oauth(monkeypatch):
    """Only a 401/auth_failed triggers the OAuth probe; other failures abort as before."""
    async def fake_discover(_url, _token, _headers, **_kw):
        raise mcp_servers.McpDiscoveryError("unreachable", "Connection failed during initialize")

    probed = {"called": False}

    async def fake_advertises(_url):
        probed["called"] = True
        return True

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)
    monkeypatch.setattr(mcp_servers, "_advertises_oauth", fake_advertises)

    db = _FakeAddSession()
    with pytest.raises(HTTPException) as exc:
        await mcp_servers.add_mcp_server(_body(), user=SimpleNamespace(id="admin"), db=db)

    assert exc.value.status_code == 400
    assert probed["called"] is False
    assert _created_servers(db) == []


@pytest.mark.asyncio
async def test_add_protocol_error_still_aborts(monkeypatch):
    async def fake_discover(_url, _token, _headers, **_kw):
        raise mcp_servers.McpDiscoveryError("protocol_error", "HTTP 500 during initialize")

    probed = {"called": False}

    async def fake_advertises(_url):
        probed["called"] = True
        return True

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)
    monkeypatch.setattr(mcp_servers, "_advertises_oauth", fake_advertises)

    db = _FakeAddSession()
    with pytest.raises(HTTPException) as exc:
        await mcp_servers.add_mcp_server(_body(), user=SimpleNamespace(id="admin"), db=db)

    assert exc.value.status_code == 400
    assert exc.value.detail == "HTTP 500 during initialize"
    assert probed["called"] is False
    assert _created_servers(db) == []


@pytest.mark.asyncio
async def test_add_successful_discovery_still_creates_enabled_server(monkeypatch):
    tools = [{"name": "search", "description": "Search docs"}]

    async def fake_discover(_url, _token, _headers, **_kw):
        return tools

    monkeypatch.setattr(mcp_servers, "_discover_tools", fake_discover)

    db = _FakeAddSession()
    payload = await mcp_servers.add_mcp_server(_body(), user=SimpleNamespace(id="admin"), db=db)

    assert payload["tools"] == tools
    assert payload["enabled"] is True
    assert payload["oauth_enabled"] is False
    assert payload["last_status"] == "ok"
    assert "needs_oauth" not in payload
    assert len(db.added) == 1
    assert db.commits == 1
    created = db.added[0]
    assert created.tools == tools
    assert created.oauth_enabled is False
    assert created.last_status == "ok"


def test_add_mcp_server_route_returns_201_created():
    route = next(
        route for route in mcp_servers.router.routes
        if getattr(route, "endpoint", None) is mcp_servers.add_mcp_server
    )
    assert route.status_code == 201


@pytest.mark.asyncio
async def test_advertises_oauth_detects_resource_metadata_challenge(monkeypatch):
    async def fake_probe(_url):
        return 'Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource"'

    monkeypatch.setattr(mcp_servers, "_oauth_probe_challenge", fake_probe)
    assert await mcp_servers._advertises_oauth("https://mcp.example.test/mcp") is True


@pytest.mark.asyncio
async def test_advertises_oauth_false_when_no_challenge(monkeypatch):
    async def fake_probe(_url):
        return None

    monkeypatch.setattr(mcp_servers, "_oauth_probe_challenge", fake_probe)
    assert await mcp_servers._advertises_oauth("https://mcp.example.test/mcp") is False


@pytest.mark.asyncio
async def test_advertises_oauth_false_on_probe_error(monkeypatch):
    async def fake_probe(_url):
        raise HTTPException(status_code=400, detail="unreachable")

    monkeypatch.setattr(mcp_servers, "_oauth_probe_challenge", fake_probe)
    assert await mcp_servers._advertises_oauth("https://mcp.example.test/mcp") is False


def test_needs_oauth_is_a_recognised_health_status():
    assert mcp_servers.MCP_HEALTH_NEEDS_OAUTH == "needs_oauth"
    assert mcp_servers.MCP_HEALTH_NEEDS_OAUTH in mcp_servers.MCP_HEALTH_STATUSES
