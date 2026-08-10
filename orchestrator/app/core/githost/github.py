"""GitHub implementation of GitHostProvider.

Wraps the GitHub REST API with the exact URLs/headers/payloads that were
previously inlined in `agent_manager` and `self_test_service` (#532 phase 1)
— behaviour for GitHub is unchanged, only the seam moved.
"""

import httpx

from app.core.githost.base import GitHostProvider


class GitHubHostProvider(GitHostProvider):
    api_base = "https://api.github.com"

    def get_agent_env(self, token: str) -> dict[str, str]:
        return {"GITHUB_TOKEN": token, "GH_TOKEN": token}

    async def search_open_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, title: str
    ) -> int | None:
        resp = await client.get(
            f"{self.api_base}/search/issues",
            params={"q": f'repo:{repo} is:issue is:open "{title}"'},
            headers=self._headers(token),
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("items", [])
        return items[0]["number"] if items else None

    async def create_issue(
        self,
        client: httpx.AsyncClient,
        token: str,
        repo: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> str | None:
        resp = await client.post(
            f"{self.api_base}/repos/{repo}/issues",
            headers={**self._headers(token), "X-GitHub-Api-Version": "2022-11-28"},
            json={"title": title, "body": body, "labels": labels},
        )
        if resp.status_code in (200, 201):
            return resp.json()["html_url"]
        return None

    async def comment_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, issue_number: int, body: str
    ) -> None:
        await client.post(
            f"{self.api_base}/repos/{repo}/issues/{issue_number}/comments",
            headers=self._headers(token),
            json={"body": body},
        )

    async def close_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, issue_number: int
    ) -> None:
        await client.patch(
            f"{self.api_base}/repos/{repo}/issues/{issue_number}",
            headers=self._headers(token),
            json={"state": "closed"},
        )

    async def list_open_issues_with_label(
        self, client: httpx.AsyncClient, token: str, repo: str, label: str
    ) -> list[dict]:
        resp = await client.get(
            f"{self.api_base}/search/issues",
            params={"q": f"repo:{repo} is:issue is:open label:{label}"},
            headers=self._headers(token),
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("items", [])

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
