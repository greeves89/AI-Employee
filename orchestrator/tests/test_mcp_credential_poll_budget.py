"""Real HTTP regression for #746: retrying providers must not block credential polls.

Adapted from the PR829 timeout probe: two 7s ConnectErrors, actual backoff,
production endpoint/manager/refresh code, controlled DB and provider transport.
"""
import asyncio
import contextlib
import socket
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException

from app.api import agents
from app.core.agent_manager import AgentManager
from app.services import mcp_oauth_refresh as refresh


def server_row():
    return SimpleNamespace(
        id=9829, name="test-server", url="https://example.invalid/mcp",
        oauth_enabled=True, auth_token_encrypted="old-access", headers_encrypted=None,
        oauth_refresh_token_encrypted="old-refresh",
        oauth_access_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        oauth_token_endpoint="https://example.invalid/token",
        oauth_client_secret_encrypted=None, oauth_client_id="example-client",
        oauth_scope="", oauth_resource=None,
    )


def session(row):
    result = Mock()
    result.scalars.return_value.all.return_value = [row]
    return SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
        execute=AsyncMock(return_value=result), get=AsyncMock(return_value=None),
        refresh=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock(),
    )


@contextlib.asynccontextmanager
async def http_endpoint(manager):
    app = FastAPI()

    @app.get("/probe")
    async def probe():
        return await agents.get_agent_mcp_credentials(
            "test-agent", manager, {"agent_id": "test-agent"})

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    server.capture_signals = contextlib.nullcontext
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/probe"
    finally:
        server.should_exit = True
        await task
        sock.close()


@pytest.mark.asyncio
async def test_delayed_connect_errors_do_not_consume_poll_budget():
    row = server_row()
    # A separate sweep session owns the refresh transaction. Its commit exposes
    # the new row to the polling session, never an uncommitted token.
    sweep_row = SimpleNamespace(**vars(row))
    poll_db, sweep_db = session(row), session(sweep_row)

    async def persist():
        vars(row).update(vars(sweep_row))

    sweep_db.commit.side_effect = persist
    manager = AgentManager(poll_db, None, None)
    manager._get_agent = AsyncMock(return_value=SimpleNamespace(config={}))
    attempts = 0

    async def provider(request):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            await asyncio.sleep(7)
            raise httpx.ConnectError("delayed connection failure", request=request)
        return httpx.Response(200, json={
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})

    real_client = httpx.AsyncClient
    refresh._recently_verified.pop(row.id, None)
    try:
        with patch("app.api.mcp_servers._assert_mcp_url_allowed", new_callable=AsyncMock), \
             patch.object(refresh.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                 transport=httpx.MockTransport(provider), trust_env=False, **kw)), \
             patch.object(refresh, "decrypt_token", side_effect=lambda x: x), \
             patch.object(refresh, "encrypt_token", side_effect=lambda x: x), \
             patch("app.core.agent_manager.decrypt_token", side_effect=lambda x: x):
            async with real_client(timeout=15, trust_env=False) as caller, http_endpoint(manager) as url:
                started = time.monotonic()
                # Old eb55c141 waits on the provider here and raises ReadTimeout.
                initial = await caller.get(url)
                elapsed = time.monotonic() - started
                assert initial.status_code == 200
                assert initial.json()["auth"] == {"test-server": "old-access"}
                assert elapsed < 10
                assert attempts == 0
                poll_db.commit.assert_not_awaited()
                print(f"initial poll: HTTP 200 in {elapsed:.2f}s; provider attempts=0")

                sweep = asyncio.create_task(refresh.refresh_all_oauth_servers(sweep_db))
                try:
                    # Read credentials while a connect retry is in flight.
                    await asyncio.sleep(.05)
                    during = await caller.get(url)
                    assert during.status_code == 200
                    assert during.json()["auth"] == {"test-server": "old-access"}
                    assert await asyncio.wait_for(sweep, 25) == 1
                finally:
                    # Do not leave a task alive if a test assertion fails.
                    if not sweep.done():
                        await sweep
                assert attempts == 3
                sweep_db.commit.assert_awaited_once()
                assert row.oauth_refresh_token_encrypted == "new-refresh"
                final = await caller.get(url)
                assert final.status_code == 200
                assert final.json()["auth"] == {"test-server": "new-access"}
                print(f"sweep persisted once after {time.monotonic() - started:.2f}s; next poll received new token")
    finally:
        refresh._recently_verified.pop(row.id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_step", ["agent", "servers", "combined"])
async def test_deadline_covers_entire_read_path(slow_step):
    manager = Mock()

    async def get_agent(_):
        await asyncio.sleep(.2 if slow_step == "agent" else .03)
        return SimpleNamespace(config={})

    async def get_env(**kwargs):
        assert kwargs["refresh_oauth"] is False
        await asyncio.sleep(.2 if slow_step == "servers" else .03)
        return {}

    manager._get_agent = AsyncMock(side_effect=get_agent)
    manager._get_custom_mcp_env = AsyncMock(side_effect=get_env)
    assert agents._MCP_CREDENTIAL_LOOKUP_TIMEOUT < 15
    with patch.object(agents, "_MCP_CREDENTIAL_LOOKUP_TIMEOUT", .05):
        with pytest.raises(HTTPException) as caught:
            await agents.get_agent_mcp_credentials("test-agent", manager, {"agent_id": "test-agent"})
    assert caught.value.status_code == 504


@pytest.mark.asyncio
async def test_startup_still_refreshes_but_poll_preserves_server_filter():
    row = server_row()
    manager = AgentManager(session(row), None, None)
    with patch.object(refresh, "refresh_if_needed", new_callable=AsyncMock) as run, \
         patch("app.core.agent_manager.decrypt_token", side_effect=lambda x: x):
        await manager._get_custom_mcp_env()
        run.assert_awaited_once_with(row, manager.db)
        run.reset_mock()
        result = await manager._get_custom_mcp_env(
            agent_config={"mcp_servers": []}, refresh_oauth=False)
        assert result == {}
        run.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_deadline_does_not_cancel_sent_refresh_or_its_commit():
    row = server_row()
    db = session(row)
    sent, respond = asyncio.Event(), asyncio.Event()

    async def provider(request):
        sent.set()  # provider has accepted the single-use refresh token
        await respond.wait()
        return httpx.Response(200, json={
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})

    async def slow_lookup(_):
        await asyncio.Event().wait()

    manager = Mock(_get_agent=AsyncMock(side_effect=slow_lookup))
    real_client = httpx.AsyncClient
    refresh._recently_verified.pop(row.id, None)
    try:
        with patch("app.api.mcp_servers._assert_mcp_url_allowed", new_callable=AsyncMock), \
             patch.object(refresh.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                 transport=httpx.MockTransport(provider), trust_env=False, **kw)), \
             patch.object(refresh, "decrypt_token", side_effect=lambda x: x), \
             patch.object(refresh, "encrypt_token", side_effect=lambda x: x), \
             patch.object(agents, "_MCP_CREDENTIAL_LOOKUP_TIMEOUT", .05):
            sweep = asyncio.create_task(refresh.refresh_if_needed(row, db))
            try:
                await asyncio.wait_for(sent.wait(), 2)
                with pytest.raises(HTTPException) as caught:
                    await agents.get_agent_mcp_credentials(
                        "test-agent", manager, {"agent_id": "test-agent"})
                assert caught.value.status_code == 504
                assert not sweep.done()
                db.commit.assert_not_awaited()
            finally:
                respond.set()
                assert await asyncio.wait_for(sweep, 2)
            db.commit.assert_awaited_once()
            assert row.oauth_refresh_token_encrypted == "new-refresh"
    finally:
        refresh._recently_verified.pop(row.id, None)
