"""Tests for #488 Phase 2: agents must pick up rotated MCP OAuth tokens without
being recreated. CUSTOM_MCP_AUTH is only ever set once at container creation, so
refresh_mcp_credentials_loop periodically re-fetches credentials from the
orchestrator and applies whatever changed."""
import asyncio
import json

import pytest

from app import main


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stand-in: one canned response per instantiation."""

    def __init__(self, response: _FakeResponse, *, timeout=None):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return self._response


async def _run_one_iteration(monkeypatch, response_payload, register_via_cli):
    """Drive refresh_mcp_credentials_loop through exactly one sleep/fetch cycle,
    then stop it by making the second sleep raise CancelledError."""
    import httpx as httpx_module

    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        httpx_module,
        "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient(_FakeResponse(200, response_payload), **kw),
    )

    with pytest.raises(asyncio.CancelledError):
        await main.refresh_mcp_credentials_loop("agent-1", register_via_cli=register_via_cli, interval_seconds=0)


@pytest.mark.asyncio
async def test_refresh_updates_env_when_token_rotates(monkeypatch):
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"srv": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"srv": "old-token"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)
    monkeypatch.setattr(main, "_run_mcp_remove", lambda name: True)
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: True)

    payload = {
        "servers": {"srv": "https://x/mcp"},
        "auth": {"srv": "new-token"},
        "headers": {},
    }
    await _run_one_iteration(monkeypatch, payload, register_via_cli=False)

    assert json.loads(main.os.environ["CUSTOM_MCP_AUTH"]) == {"srv": "new-token"}


@pytest.mark.asyncio
async def test_refresh_reregisters_via_cli_when_token_rotates(monkeypatch):
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"srv": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"srv": "old-token"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)

    removed = []
    added = []
    monkeypatch.setattr(main, "_run_mcp_remove", lambda name: removed.append(name) or True)
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: added.append(args) or True)

    payload = {
        "servers": {"srv": "https://x/mcp"},
        "auth": {"srv": "new-token"},
        "headers": {},
    }
    await _run_one_iteration(monkeypatch, payload, register_via_cli=True)

    assert removed == ["srv"]
    assert len(added) == 1
    assert "Authorization: Bearer new-token" in added[0]


@pytest.mark.asyncio
async def test_refresh_skips_cli_calls_when_nothing_changed(monkeypatch):
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"srv": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"srv": "same-token"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)

    removed = []
    added = []
    monkeypatch.setattr(main, "_run_mcp_remove", lambda name: removed.append(name) or True)
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: added.append(args) or True)

    payload = {
        "servers": {"srv": "https://x/mcp"},
        "auth": {"srv": "same-token"},
        "headers": {},
    }
    await _run_one_iteration(monkeypatch, payload, register_via_cli=True)

    assert removed == []
    assert added == []


@pytest.mark.asyncio
async def test_refresh_tolerates_http_error(monkeypatch):
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"srv": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"srv": "old-token"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)

    called = []
    monkeypatch.setattr(main, "_run_mcp_remove", lambda name: called.append(name) or True)
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: called.append(args) or True)

    # 500 response: loop must not crash and must not touch the CLI.
    await _run_one_iteration(monkeypatch, {}, register_via_cli=True)
    import httpx as httpx_module
    monkeypatch.setattr(
        httpx_module, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(_FakeResponse(500, {}), **kw)
    )
    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await main.refresh_mcp_credentials_loop("agent-1", register_via_cli=True, interval_seconds=0)

    assert called == []


@pytest.mark.asyncio
async def test_refresh_retries_on_next_tick_when_add_fails(monkeypatch):
    """#501: a failed `claude mcp add` must NOT be recorded as applied — the next
    poll tick must re-detect the unchanged rotation and retry, instead of leaving
    the server de-registered until container restart."""
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"srv": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"srv": "old-token"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)

    added = []
    # First add fails, second add succeeds.
    add_results = iter([False, True])
    monkeypatch.setattr(main, "_run_mcp_remove", lambda name: True)
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: added.append(args) or next(add_results))

    payload = {"servers": {"srv": "https://x/mcp"}, "auth": {"srv": "new-token"}, "headers": {}}

    import httpx as httpx_module
    monkeypatch.setattr(
        httpx_module,
        "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient(_FakeResponse(200, payload), **kw),
    )

    # Allow exactly two fetch cycles, then cancel on the third sleep.
    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main.refresh_mcp_credentials_loop("agent-1", register_via_cli=True, interval_seconds=0)

    # Two add attempts: the failed one and the retry on the next tick.
    assert len(added) == 2


@pytest.mark.asyncio
async def test_refresh_no_retry_after_successful_add(monkeypatch):
    """A successful add must be recorded so the next tick sees no change and does
    not needlessly re-register the server."""
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"srv": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"srv": "old-token"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)

    added = []
    monkeypatch.setattr(main, "_run_mcp_remove", lambda name: True)
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: added.append(args) or True)

    payload = {"servers": {"srv": "https://x/mcp"}, "auth": {"srv": "new-token"}, "headers": {}}

    import httpx as httpx_module
    monkeypatch.setattr(
        httpx_module,
        "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient(_FakeResponse(200, payload), **kw),
    )

    sleep_calls = {"n": 0}

    async def fake_sleep(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main.refresh_mcp_credentials_loop("agent-1", register_via_cli=True, interval_seconds=0)

    # Only one add: the second tick sees an unchanged token and skips.
    assert len(added) == 1


def test_run_mcp_remove_uses_scope_user(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    assert main._run_mcp_remove("srv") is True
    assert calls[0] == ["claude", "mcp", "remove", "srv", "--scope", "user"]
