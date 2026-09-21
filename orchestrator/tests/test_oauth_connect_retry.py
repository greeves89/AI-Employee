"""Connect-only retry limits and real OAuth call-site coverage (#746)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.oauth_retry import post_refresh_with_retry


@pytest.mark.asyncio
async def test_connect_retry_backoff_then_success(caplog):
    calls = []

    def transport(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ConnectError("secret endpoint detail", request=request)
        return httpx.Response(200, json={"access_token": "new"})

    with patch("app.core.oauth_retry.asyncio.sleep", new_callable=AsyncMock) as sleep, \
         patch("app.core.oauth_retry.random.uniform", return_value=.1):
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            response = await post_refresh_with_retry(
                client, "https://example.invalid/token", data={"refresh_token": "sensitive"})
    assert response.status_code == 200
    assert len(calls) == 3
    assert [c.args[0] for c in sleep.await_args_list] == [.6, 1.1]
    assert all(r.content == calls[0].content for r in calls)
    assert "secret endpoint detail" not in caplog.text
    assert "sensitive" not in caplog.text
    assert "example.invalid" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_connect_retry_is_bounded(error_type):
    error = error_type("down")
    client = SimpleNamespace(post=AsyncMock(side_effect=error))
    with patch("app.core.oauth_retry.asyncio.sleep", new_callable=AsyncMock) as sleep:
        with pytest.raises(error_type) as caught:
            await post_refresh_with_retry(client, "https://example.invalid/token")
    assert caught.value is error
    assert client.post.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.WriteError, httpx.PoolTimeout,
                                        httpx.RemoteProtocolError, asyncio.CancelledError])
async def test_ambiguous_failures_and_cancellation_are_not_retried(error_type):
    client = SimpleNamespace(post=AsyncMock(side_effect=error_type("failure")))
    with patch("app.core.oauth_retry.asyncio.sleep", new_callable=AsyncMock) as sleep:
        with pytest.raises(error_type):
            await post_refresh_with_retry(client, "https://example.invalid/token")
    assert client.post.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_http_response_is_not_retried(status):
    response = httpx.Response(status)
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    assert await post_refresh_with_retry(client, "https://example.invalid/token") is response
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_during_backoff_stops_retry():
    client = SimpleNamespace(post=AsyncMock(side_effect=httpx.ConnectError("down")))
    with patch("app.core.oauth_retry.asyncio.sleep", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await post_refresh_with_retry(client, "https://example.invalid/token")
    client.post.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["anthropic_oauth", "standard"])
async def test_integration_refresh_retries_and_persists_once(method):
    from app.services.oauth_service import OAuthService
    provider = SimpleNamespace(token_exchange_method=method, token_url="https://example.invalid/token")
    integration = SimpleNamespace(provider=SimpleNamespace(value="test"), user_id="test",
                                  refresh_token_encrypted="old")
    client = AsyncMock()
    client.post.side_effect = [httpx.ConnectError("dns"), httpx.Response(200, json={
        "access_token": "access", "refresh_token": "rotated", "expires_in": 3600})]
    client.__aenter__.return_value = client
    db = AsyncMock()
    with patch("app.services.oauth_service.get_provider", return_value=provider), \
         patch("app.services.oauth_service.get_provider_client_id", return_value="id"), \
         patch("app.services.oauth_service.get_provider_client_secret", return_value="secret"), \
         patch("app.services.oauth_service.decrypt_token", side_effect=lambda v: v), \
         patch("app.services.oauth_service.encrypt_token", side_effect=lambda v: "enc:" + v), \
         patch("app.services.oauth_service.httpx.AsyncClient", return_value=client), \
         patch("app.core.oauth_retry.asyncio.sleep", new_callable=AsyncMock):
        await OAuthService(db, None)._refresh_token(integration)
    assert client.post.await_count == 2
    assert integration.refresh_token_encrypted == "enc:rotated"
    assert integration.access_token_encrypted == "enc:access"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(integration)


@pytest.mark.asyncio
@pytest.mark.parametrize("grant,attempts", [("refresh_token", 3), ("authorization_code", 1)])
async def test_mcp_refresh_retry_preserves_guard_and_code_exchange(grant, attempts):
    from app.services.mcp_oauth_refresh import perform_token_request, OAuthTokenError
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = httpx.ConnectError("dns")
    with patch("app.api.mcp_servers._assert_mcp_url_allowed", new_callable=AsyncMock) as guard, \
         patch("app.services.mcp_oauth_refresh.httpx.AsyncClient", return_value=client) as factory, \
         patch("app.core.oauth_retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(OAuthTokenError):
            await perform_token_request("https://example.invalid/token", {"grant_type": grant})
    guard.assert_awaited_once_with("https://example.invalid/token")
    factory.assert_called_once_with(timeout=15.0)
    assert client.post.await_count == attempts
    assert all(c.kwargs["follow_redirects"] is False for c in client.post.await_args_list)


@pytest.mark.asyncio
async def test_mcp_guard_failure_never_starts_http_retry():
    from app.services.mcp_oauth_refresh import perform_token_request
    with patch("app.api.mcp_servers._assert_mcp_url_allowed", side_effect=ValueError("blocked")), \
         patch("app.services.mcp_oauth_refresh.httpx.AsyncClient") as factory:
        with pytest.raises(ValueError, match="blocked"):
            await perform_token_request("https://example.invalid/token", {"grant_type": "refresh_token"})
    factory.assert_not_called()
