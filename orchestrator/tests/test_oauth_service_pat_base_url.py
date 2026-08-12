"""Regression test: OAuthService.store_pat routes GitHub Enterprise Server
(base_url) PATs through the GHES userinfo endpoint and persists host_type/
base_url on the resulting OAuthIntegration row (#532 phase 2).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.oauth_integration import OAuthIntegration
from app.services.oauth_service import OAuthService


def _db_with_integration(integration=None):
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = integration
    db.execute = AsyncMock(return_value=exec_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


def _userinfo_resp(status_code=200, login="octocat"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"login": login}
    return resp


def _client_ctx(resp):
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_store_pat_with_base_url_hits_ghes_userinfo_endpoint():
    db = _db_with_integration(None)
    service = OAuthService(db=db, redis=MagicMock())
    resp = _userinfo_resp(login="enterprise-user")
    ctx, client = _client_ctx(resp)

    with patch("app.services.oauth_service.httpx.AsyncClient", return_value=ctx):
        integration = await service.store_pat(
            "github", "ghp_tok", base_url="https://ghe.example.com"
        )

    args, _ = client.get.call_args
    assert args[0] == "https://ghe.example.com/api/v3/user"
    assert integration.host_type == "github"
    assert integration.base_url == "https://ghe.example.com"
    assert integration.account_label == "enterprise-user"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_store_pat_without_base_url_hits_public_github_userinfo():
    db = _db_with_integration(None)
    service = OAuthService(db=db, redis=MagicMock())
    resp = _userinfo_resp(login="octocat")
    ctx, client = _client_ctx(resp)

    with patch("app.services.oauth_service.httpx.AsyncClient", return_value=ctx):
        integration = await service.store_pat("github", "ghp_tok")

    args, _ = client.get.call_args
    assert args[0] == "https://api.github.com/user"
    assert integration.host_type is None
    assert integration.base_url is None


@pytest.mark.asyncio
async def test_store_pat_updates_existing_integration_with_base_url():
    existing = OAuthIntegration(
        provider="github",
        access_token_encrypted="old-blob",
        host_type=None,
        base_url=None,
    )
    db = _db_with_integration(existing)
    service = OAuthService(db=db, redis=MagicMock())
    resp = _userinfo_resp(login="enterprise-user")
    ctx, client = _client_ctx(resp)

    with patch("app.services.oauth_service.httpx.AsyncClient", return_value=ctx):
        integration = await service.store_pat(
            "github", "ghp_new", base_url="https://ghe.example.com"
        )

    assert integration is existing
    assert integration.host_type == "github"
    assert integration.base_url == "https://ghe.example.com"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_store_pat_invalid_token_raises_value_error():
    db = _db_with_integration(None)
    service = OAuthService(db=db, redis=MagicMock())
    resp = _userinfo_resp(status_code=401)
    ctx, client = _client_ctx(resp)

    with patch("app.services.oauth_service.httpx.AsyncClient", return_value=ctx):
        with pytest.raises(ValueError):
            await service.store_pat("github", "bad_tok", base_url="https://ghe.example.com")
