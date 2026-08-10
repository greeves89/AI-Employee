"""Git-host provider interface.

Phase 1 of #532: extracts the operations that were previously GitHub-only
and hardcoded into `agent_manager._get_integration_env` and
`self_test_service`'s issue-creation code into a provider interface. GitHub
is the only implementation for now (see `github.py`); Forgejo/GitLab/Gitea
adapters land in later phases behind the same interface, unchanged for
callers.
"""

from abc import ABC, abstractmethod

import httpx


class GitHostProvider(ABC):
    """Operations agents and the orchestrator perform against a Git host."""

    @abstractmethod
    def get_agent_env(self, token: str) -> dict[str, str]:
        """Env vars an agent container needs to clone/push/open PRs against this host."""

    @abstractmethod
    async def search_open_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, title: str
    ) -> int | None:
        """Return the issue number of an open issue with this exact title, or None."""

    @abstractmethod
    async def create_issue(
        self,
        client: httpx.AsyncClient,
        token: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> str | None:
        """Create an issue, return its html_url, or None on failure."""

    @abstractmethod
    async def comment_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, issue_number: int, body: str
    ) -> None:
        """Post a comment on an existing issue."""

    @abstractmethod
    async def close_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, issue_number: int
    ) -> None:
        """Close an issue."""

    @abstractmethod
    async def list_open_issues_with_label(
        self, client: httpx.AsyncClient, token: str, repo: str, label: str
    ) -> list[dict]:
        """Return open issues carrying the given label."""
