"""Pluggable Git-host providers: GitHub (auch Enterprise), Forgejo/Gitea, GitLab (#532, #1194)."""

from app.core.githost.base import GitHostProvider
from app.core.githost.registry import get_git_host_provider

__all__ = ["GitHostProvider", "get_git_host_provider"]
