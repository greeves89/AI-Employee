"""Resolve a GitHostProvider by host type.

Only "github" exists today (#532 phase 1). An unknown/unset host_type falls
back to GitHub — this preserves current behaviour, where GitHub was the only
option, for every existing caller.
"""

from app.core.githost.base import GitHostProvider
from app.core.githost.github import GitHubHostProvider

_PROVIDERS: dict[str, GitHostProvider] = {
    "github": GitHubHostProvider(),
}


def get_git_host_provider(host_type: str = "github") -> GitHostProvider:
    return _PROVIDERS.get(host_type, _PROVIDERS["github"])
