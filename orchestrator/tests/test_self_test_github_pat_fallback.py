"""Tests for the GITHUB_PAT fallback in SelfTestService._create_github_issues (issue #316).

When no user has connected a GitHub OAuth account, the service must fall back to
the GITHUB_PAT environment variable instead of silently skipping issue creation.
The warning is only logged when BOTH the OAuth integration and GITHUB_PAT are absent.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.self_test_service import SelfTestService, TestResult


def _failed_result() -> TestResult:
    r = TestResult(name="db_connectivity", category="integration")
    r.status = "failed"
    r.error = "connection refused"
    return r


def _db_with_oauth(oauth):
    """AsyncSession stub whose execute(...).scalar_one_or_none() returns *oauth*."""
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = oauth
    db.execute = AsyncMock(return_value=exec_result)
    return db


def _http_client_ctx(search_status=200, search_total=0, create_status=201):
    """Mock httpx.AsyncClient context manager for the happy path (no existing issue)."""
    client = AsyncMock()

    search_resp = MagicMock()
    search_resp.status_code = search_status
    search_resp.json.return_value = {"total_count": search_total, "items": []}
    client.get = AsyncMock(return_value=search_resp)

    create_resp = MagicMock()
    create_resp.status_code = create_status
    create_resp.json.return_value = {"html_url": "https://github.com/greeves89/AI-Employee/issues/999"}
    client.post = AsyncMock(return_value=create_resp)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_falls_back_to_github_pat_when_no_oauth(monkeypatch):
    """OAuth integration absent + GITHUB_PAT set → issue creation proceeds with the env token."""
    monkeypatch.setenv("GITHUB_PAT", "ghp_envtoken123")
    db = _db_with_oauth(None)
    ctx, client = _http_client_ctx()

    with patch("app.services.self_test_service.httpx.AsyncClient", return_value=ctx):
        created = await SelfTestService()._create_github_issues(
            db, [_failed_result()], test_run_id=42
        )

    assert created == 1
    # The env PAT must be the bearer token on the create call.
    _, kwargs = client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer ghp_envtoken123"


@pytest.mark.asyncio
async def test_warns_and_returns_zero_when_both_absent(monkeypatch, caplog):
    """No OAuth integration AND no GITHUB_PAT → warning logged, nothing created."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    db = _db_with_oauth(None)

    with patch("app.services.self_test_service.httpx.AsyncClient") as client_cls:
        with caplog.at_level("WARNING"):
            created = await SelfTestService()._create_github_issues(
                db, [_failed_result()], test_run_id=42
            )

    assert created == 0
    client_cls.assert_not_called()  # never reached the HTTP client
    assert any("GITHUB_PAT" in rec.message for rec in caplog.records)
