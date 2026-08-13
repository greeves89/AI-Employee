"""Tests for the GitHostProvider interface (#532 phase 1).

Verifies GitHubHostProvider reproduces the exact HTTP calls (URL, headers,
payload) that were previously inlined in agent_manager and self_test_service,
so extracting the interface did not change GitHub behaviour.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.githost.github import GitHubHostProvider
from app.core.githost.registry import get_git_host_provider


def test_registry_returns_github_by_default():
    assert isinstance(get_git_host_provider(), GitHubHostProvider)
    assert isinstance(get_git_host_provider("github"), GitHubHostProvider)


def test_registry_falls_back_to_github_for_unknown_host():
    # No other host is registered yet (phase 1) — unknown types must not crash.
    assert isinstance(get_git_host_provider("forgejo"), GitHubHostProvider)


def test_get_agent_env_sets_both_token_vars():
    env = GitHubHostProvider().get_agent_env("test_pat_abc123")
    assert env == {"GITHUB_TOKEN": "test_pat_abc123", "GH_TOKEN": "test_pat_abc123"}


# --- #532 phase 2: base_url (GitHub Enterprise Server) ---


def test_registry_with_base_url_routes_to_ghes_api():
    provider = get_git_host_provider("github", "https://ghe.example.com")
    assert isinstance(provider, GitHubHostProvider)
    assert provider.api_base == "https://ghe.example.com/api/v3"
    assert provider.host == "ghe.example.com"


def test_registry_without_base_url_still_uses_public_github():
    provider = get_git_host_provider("github", None)
    assert provider.api_base == "https://api.github.com"
    assert provider.host is None


def test_registry_base_url_trailing_slash_stripped():
    provider = get_git_host_provider("github", "https://ghe.example.com/")
    assert provider.api_base == "https://ghe.example.com/api/v3"


def test_get_agent_env_sets_gh_host_for_ghes():
    provider = GitHubHostProvider(api_base="https://ghe.example.com/api/v3", host="ghe.example.com")
    env = provider.get_agent_env("tok")
    assert env == {"GITHUB_TOKEN": "tok", "GH_TOKEN": "tok", "GH_HOST": "ghe.example.com"}


@pytest.mark.asyncio
async def test_ghes_provider_hits_enterprise_api_base():
    client = _client(get_resp=_resp(200, {"items": [{"number": 5}]}))
    provider = GitHubHostProvider(api_base="https://ghe.example.com/api/v3", host="ghe.example.com")

    result = await provider.search_open_issue(client, "tok", "org/repo", "x")

    assert result == 5
    args, _ = client.get.call_args
    assert args[0] == "https://ghe.example.com/api/v3/search/issues"


def _client(get_resp=None, post_resp=None, patch_resp=None):
    client = AsyncMock()
    if get_resp is not None:
        client.get = AsyncMock(return_value=get_resp)
    if post_resp is not None:
        client.post = AsyncMock(return_value=post_resp)
    if patch_resp is not None:
        client.patch = AsyncMock(return_value=patch_resp)
    return client


def _resp(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    return r


@pytest.mark.asyncio
async def test_search_open_issue_found():
    client = _client(get_resp=_resp(200, {"items": [{"number": 42}]}))
    provider = GitHubHostProvider()

    result = await provider.search_open_issue(client, "tok", "org/repo", "[Self-Test] x")

    assert result == 42
    args, kwargs = client.get.call_args
    assert args[0] == "https://api.github.com/search/issues"
    assert kwargs["params"]["q"] == 'repo:org/repo is:issue is:open "[Self-Test] x"'
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_search_open_issue_not_found():
    client = _client(get_resp=_resp(200, {"items": []}))
    result = await GitHubHostProvider().search_open_issue(client, "tok", "org/repo", "x")
    assert result is None


@pytest.mark.asyncio
async def test_search_open_issue_non_200_treated_as_not_found():
    client = _client(get_resp=_resp(503, {}))
    result = await GitHubHostProvider().search_open_issue(client, "tok", "org/repo", "x")
    assert result is None


@pytest.mark.asyncio
async def test_create_issue_success():
    client = _client(post_resp=_resp(201, {"html_url": "https://github.com/org/repo/issues/9"}))
    provider = GitHubHostProvider()

    url = await provider.create_issue(client, "tok", "org/repo", "title", "body", ["auto-test"])

    assert url == "https://github.com/org/repo/issues/9"
    args, kwargs = client.post.call_args
    assert args[0] == "https://api.github.com/repos/org/repo/issues"
    assert kwargs["json"] == {"title": "title", "body": "body", "labels": ["auto-test"]}
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"


@pytest.mark.asyncio
async def test_create_issue_failure_returns_none():
    client = _client(post_resp=_resp(422, {}))
    url = await GitHubHostProvider().create_issue(client, "tok", "org/repo", "t", "b", [])
    assert url is None


@pytest.mark.asyncio
async def test_comment_issue_posts_body():
    client = _client(post_resp=_resp(201, {}))
    await GitHubHostProvider().comment_issue(client, "tok", "org/repo", 7, "hello")
    args, kwargs = client.post.call_args
    assert args[0] == "https://api.github.com/repos/org/repo/issues/7/comments"
    assert kwargs["json"] == {"body": "hello"}


@pytest.mark.asyncio
async def test_close_issue_patches_state():
    client = _client(patch_resp=_resp(200, {}))
    await GitHubHostProvider().close_issue(client, "tok", "org/repo", 7)
    args, kwargs = client.patch.call_args
    assert args[0] == "https://api.github.com/repos/org/repo/issues/7"
    assert kwargs["json"] == {"state": "closed"}


@pytest.mark.asyncio
async def test_list_open_issues_with_label():
    client = _client(get_resp=_resp(200, {"items": [{"number": 1, "title": "[Self-Test] x"}]}))
    items = await GitHubHostProvider().list_open_issues_with_label(client, "tok", "org/repo", "auto-test")
    assert items == [{"number": 1, "title": "[Self-Test] x"}]
    args, kwargs = client.get.call_args
    assert kwargs["params"]["q"] == "repo:org/repo is:issue is:open label:auto-test"


@pytest.mark.asyncio
async def test_list_open_issues_with_label_non_200_returns_empty():
    client = _client(get_resp=_resp(500, {}))
    items = await GitHubHostProvider().list_open_issues_with_label(client, "tok", "org/repo", "auto-test")
    assert items == []
