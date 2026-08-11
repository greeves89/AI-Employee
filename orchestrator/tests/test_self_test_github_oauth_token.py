"""Regression test for the OAuth-DB-token path in SelfTestService (#532 phase 1).

Before this fix, `_create_github_issues` / `_auto_close_fixed_issues` read a
nonexistent `oauth.access_token` attribute and imported the nonexistent module
`app.security.encryption` whenever a GitHub OAuth integration row existed in
the DB. Both raised AttributeError/ImportError that the surrounding
try/except silently swallowed, so issue creation/closing always fell back to
"skip" for any deployment using OAuth (as opposed to the GITHUB_PAT env var).
These tests exercise that path directly and would have caught the bug.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.self_test_service import SelfTestService, TestResult


def _failed_result() -> TestResult:
    r = TestResult(name="db_connectivity", category="integration")
    r.status = "failed"
    r.error = "connection refused"
    return r


def _passed_result(name="db_connectivity") -> TestResult:
    r = TestResult(name=name, category="integration")
    r.status = "passed"
    return r


def _oauth_row():
    oauth = MagicMock()
    oauth.access_token_encrypted = "encrypted-blob"
    return oauth


def _db_with_oauth(oauth):
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = oauth
    db.execute = AsyncMock(return_value=exec_result)
    return db


def _http_client_ctx(search_status=200, search_items=None, create_status=201):
    client = AsyncMock()

    search_resp = MagicMock()
    search_resp.status_code = search_status
    search_resp.json.return_value = {"items": search_items or []}
    client.get = AsyncMock(return_value=search_resp)

    create_resp = MagicMock()
    create_resp.status_code = create_status
    create_resp.json.return_value = {"html_url": "https://github.com/greeves89/AI-Employee/issues/999"}
    client.post = AsyncMock(return_value=create_resp)

    client.patch = AsyncMock(return_value=MagicMock(status_code=200))

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_create_issue_uses_decrypted_oauth_token():
    """A GitHub OAuth integration in the DB must decrypt via access_token_encrypted."""
    db = _db_with_oauth(_oauth_row())
    ctx, client = _http_client_ctx()

    with patch("app.services.self_test_service.httpx.AsyncClient", return_value=ctx), \
         patch("app.services.self_test_service.decrypt_token", return_value="test_pat_decrypted") as mock_decrypt:
        created = await SelfTestService()._create_github_issues(
            db, [_failed_result()], test_run_id=42
        )

    mock_decrypt.assert_called_once_with("encrypted-blob")
    assert created == 1
    _, kwargs = client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test_pat_decrypted"


@pytest.mark.asyncio
async def test_auto_close_uses_decrypted_oauth_token():
    db = _db_with_oauth(_oauth_row())
    ctx, client = _http_client_ctx(
        search_items=[{"number": 5, "title": "[Self-Test] db_connectivity"}]
    )

    with patch("app.services.self_test_service.httpx.AsyncClient", return_value=ctx), \
         patch("app.services.self_test_service.decrypt_token", return_value="test_pat_decrypted") as mock_decrypt:
        closed = await SelfTestService()._auto_close_fixed_issues(
            db, [_passed_result()]
        )

    mock_decrypt.assert_called_once_with("encrypted-blob")
    assert closed == 1
    # comment then close — both bearer-authenticated with the decrypted token
    _, comment_kwargs = client.post.call_args
    assert comment_kwargs["headers"]["Authorization"] == "Bearer test_pat_decrypted"
    _, patch_kwargs = client.patch.call_args
    assert patch_kwargs["headers"]["Authorization"] == "Bearer test_pat_decrypted"
    assert patch_kwargs["json"] == {"state": "closed"}


@pytest.mark.asyncio
async def test_auto_close_returns_zero_without_oauth_row():
    db = _db_with_oauth(None)
    closed = await SelfTestService()._auto_close_fixed_issues(db, [_passed_result()])
    assert closed == 0
