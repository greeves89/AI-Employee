"""Resolve a GitHostProvider by host type.

"github" (#532 phase 1) and "forgejo"/"gitea" (phase 3, ``forgejo.py``). An
unknown/unset host_type falls back to GitHub — this preserves the behaviour
every existing caller had when GitHub was the only option. Forgejo/Gitea need a
``base_url`` (their own server); without one there is nothing to talk to, so
they fall back to GitHub as before.

`base_url` (#532 phase 2) lets a "github" integration point at a GitHub
Enterprise Server instance instead of the public API — GHES exposes the
same REST API under `<base_url>/api/v3`.
"""

from app.core.githost.base import GitHostProvider
from app.core.githost.forgejo import ForgejoHostProvider
from app.core.githost.github import GitHubHostProvider

#: Host-Typen, die die Gitea-API sprechen.
FORGEJO_TYPEN = ("forgejo", "gitea")

_DEFAULT_GITHUB = GitHubHostProvider()
_PROVIDERS: dict[str, GitHostProvider] = {
    "github": _DEFAULT_GITHUB,
}


def get_git_host_provider(host_type: str = "github", base_url: str | None = None) -> GitHostProvider:
    if not base_url:
        return _PROVIDERS.get(host_type, _DEFAULT_GITHUB)
    if host_type in FORGEJO_TYPEN:
        return ForgejoHostProvider(base_url)
    host = base_url.rstrip("/").split("://", 1)[-1]
    return GitHubHostProvider(api_base=f"{base_url.rstrip('/')}/api/v3", host=host)
