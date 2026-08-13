"""Resolve a GitHostProvider by host type.

Only "github" exists today (#532 phase 1). An unknown/unset host_type falls
back to GitHub — this preserves current behaviour, where GitHub was the only
option, for every existing caller. Forgejo/Gitea adapters land in phase 3;
until then a non-github host_type with a base_url still gets routed through
GitHubHostProvider, which is wrong for those hosts but no worse than today
(no adapter existed at all).

`base_url` (#532 phase 2) lets a "github" integration point at a GitHub
Enterprise Server instance instead of the public API — GHES exposes the
same REST API under `<base_url>/api/v3`.
"""

from app.core.githost.base import GitHostProvider
from app.core.githost.github import GitHubHostProvider

_DEFAULT_GITHUB = GitHubHostProvider()
_PROVIDERS: dict[str, GitHostProvider] = {
    "github": _DEFAULT_GITHUB,
}


def get_git_host_provider(host_type: str = "github", base_url: str | None = None) -> GitHostProvider:
    if not base_url:
        return _PROVIDERS.get(host_type, _DEFAULT_GITHUB)
    host = base_url.rstrip("/").split("://", 1)[-1]
    return GitHubHostProvider(api_base=f"{base_url.rstrip('/')}/api/v3", host=host)
