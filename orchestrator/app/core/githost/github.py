"""GitHub implementation of GitHostProvider.

Wraps the GitHub REST API with the exact URLs/headers/payloads that were
previously inlined in `agent_manager` and `self_test_service` (#532 phase 1)
— behaviour for GitHub is unchanged, only the seam moved.
"""

import httpx

from app.core.githost.base import GitHostProvider


class GitHubHostProvider(GitHostProvider):
    def __init__(self, api_base: str = "https://api.github.com", host: str | None = None):
        self.api_base = api_base
        # `gh` needs GH_HOST (bare hostname, no scheme) to target a GitHub
        # Enterprise Server instance instead of github.com.
        self.host = host

    def get_agent_env(self, token: str) -> dict[str, str]:
        env = {"GITHUB_TOKEN": token, "GH_TOKEN": token}
        if self.host:
            env["GH_HOST"] = self.host
        return env

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

    # ── Projekt-Board (core/projekt_git.py) ──

    async def _seiten(self, client: httpx.AsyncClient, token: str, pfad: str, params: dict, hoechstens: int) -> list[dict]:
        """Alle Seiten einer Liste bis ``hoechstens`` Einträge. Fehler → Ausnahme (nie still leer)."""
        ergebnis: list[dict] = []
        seite = 1
        while len(ergebnis) < hoechstens:
            resp = await client.get(
                f"{self.api_base}{pfad}", headers=self._headers(token),
                params={**params, "per_page": 100, "page": seite},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"GitHub {resp.status_code} für {pfad}")
            daten = resp.json()
            if not daten:
                break
            ergebnis.extend(daten)
            if len(daten) < 100:
                break
            seite += 1
        return ergebnis[:hoechstens]

    async def list_open_issues(self, client: httpx.AsyncClient, token: str, repo: str, hoechstens: int = 300) -> list[dict]:
        # Die Issue-Liste von GitHub enthält auch Pull Requests — die kommen extra.
        rows = await self._seiten(client, token, f"/repos/{repo}/issues", {"state": "open"}, hoechstens)
        return [r for r in rows if "pull_request" not in r]

    async def list_open_pulls(self, client: httpx.AsyncClient, token: str, repo: str, hoechstens: int = 30) -> list[dict]:
        return await self._seiten(client, token, f"/repos/{repo}/pulls", {"state": "open"}, hoechstens)

    async def pull_state(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int) -> str | None:
        resp = await client.get(f"{self.api_base}/repos/{repo}/pulls/{nummer}", headers=self._headers(token))
        if resp.status_code != 200:
            return None
        daten = resp.json()
        if daten.get("merged_at"):
            return "gemergt"
        return "geschlossen" if daten.get("state") == "closed" else "offen"

    async def issue_state(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int) -> str | None:
        resp = await client.get(f"{self.api_base}/repos/{repo}/issues/{nummer}", headers=self._headers(token))
        if resp.status_code != 200:
            return None
        return resp.json().get("state")

    async def checks_state(self, client: httpx.AsyncClient, token: str, repo: str, sha: str) -> str:
        """offen (keine Prüfungen) | ci_laeuft | ci_rot | ci_gruen."""
        resp = await client.get(
            f"{self.api_base}/repos/{repo}/commits/{sha}/check-runs", headers=self._headers(token),
            params={"per_page": 100},
        )
        if resp.status_code != 200:
            return "offen"
        laeufe = resp.json().get("check_runs") or []
        if not laeufe:
            return "offen"
        if any(r.get("status") != "completed" for r in laeufe):
            return "ci_laeuft"
        rot = {"failure", "timed_out", "cancelled", "action_required"}
        return "ci_rot" if any(r.get("conclusion") in rot for r in laeufe) else "ci_gruen"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
