"""Forgejo/Gitea implementation of GitHostProvider (#532 phase 3).

Forgejo is a Gitea fork; both speak the same REST API under ``<base>/api/v1``.
It is close to GitHub's but not identical, and the differences are exactly the
ones that matter for the project board:

* paging is ``limit``/``page`` (at most 50 per page by default), not ``per_page``;
* the issue list mixes in pull requests unless ``type=issues`` is passed;
* CI is reported as commit **statuses** (Forgejo Actions write them), not GitHub
  check runs — ``/commits/{sha}/status`` returns the combined state;
* a token goes in ``Authorization: token <t>``.

Issue and PR objects carry the same fields the board reads (``number``,
``title``, ``body``, ``html_url``, ``labels[].name``, ``head.sha``), so
``core/projekt_git`` works with this provider unchanged.
"""

import json

import httpx

from app.core.githost.base import GitHostProvider

#: Forgejo/Gitea cap a page at 50 items unless the server raises MAX_RESPONSE_ITEMS.
SEITE = 50
#: Hoechstens so viel liest die Plattform je Antwort. Der Server ist frei
#: eingetragen — eine endlose Antwort darf den Speicher nicht fuellen.
MAX_ANTWORT_BYTES = 2_000_000


async def _json_holen(client: httpx.AsyncClient, url: str, headers: dict, params: dict | None = None):
    """GET mit Groessengrenze; liefert ``(status, json_oder_None)``.

    Weiterleitungen werden NICHT verfolgt: sie fuehrten den Token zu einem
    anderen Ziel als dem geprueften.
    """
    async with client.stream("GET", url, headers=headers, params=params, follow_redirects=False) as resp:
        if resp.status_code != 200:
            return resp.status_code, None
        teile, groesse = [], 0
        async for stueck in resp.aiter_bytes():
            groesse += len(stueck)
            if groesse > MAX_ANTWORT_BYTES:
                raise RuntimeError(f"Antwort von {resp.url.host} ist zu gross")
            teile.append(stueck)
    try:
        return 200, json.loads(b"".join(teile) or b"null")
    except ValueError as e:
        raise RuntimeError(f"Antwort von {resp.url.host} ist kein JSON") from e


class ForgejoHostProvider(GitHostProvider):
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v1"
        self.host = self.base_url.split("://", 1)[-1]
        #: Fuer Fehlermeldungen: welcher Server hat geantwortet (oder nicht).
        self.anzeigename = self.host

    def get_agent_env(self, token: str) -> dict[str, str]:
        # `tea` und die Forgejo-Werkzeuge der Agenten lesen diese Variablen.
        return {"GITEA_TOKEN": token, "FORGEJO_TOKEN": token,
                "GITEA_SERVER_URL": self.base_url, "FORGEJO_SERVER_URL": self.base_url}

    async def search_open_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, title: str
    ) -> int | None:
        resp = await client.get(
            f"{self.api_base}/repos/{repo}/issues", headers=self._headers(token),
            params={"state": "open", "type": "issues", "q": title, "limit": SEITE},
        )
        if resp.status_code != 200:
            return None
        for issue in resp.json() or []:
            if issue.get("title") == title:
                return issue.get("number")
        return None

    async def create_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, title: str, body: str, labels: list[str],
    ) -> str | None:
        # Labels erwartet Gitea als Kennungen, nicht als Namen — ohne Nachschlagen
        # lieber keine setzen als das Anlegen scheitern lassen.
        resp = await client.post(
            f"{self.api_base}/repos/{repo}/issues", headers=self._headers(token),
            json={"title": title, "body": body},
        )
        if resp.status_code in (200, 201):
            return resp.json().get("html_url")
        return None

    async def comment_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, issue_number: int, body: str
    ) -> None:
        await client.post(
            f"{self.api_base}/repos/{repo}/issues/{issue_number}/comments",
            headers=self._headers(token), json={"body": body},
        )

    async def close_issue(
        self, client: httpx.AsyncClient, token: str, repo: str, issue_number: int
    ) -> None:
        await client.patch(
            f"{self.api_base}/repos/{repo}/issues/{issue_number}",
            headers=self._headers(token), json={"state": "closed"},
        )

    async def list_open_issues_with_label(
        self, client: httpx.AsyncClient, token: str, repo: str, label: str
    ) -> list[dict]:
        resp = await client.get(
            f"{self.api_base}/repos/{repo}/issues", headers=self._headers(token),
            params={"state": "open", "type": "issues", "labels": label, "limit": SEITE},
        )
        if resp.status_code != 200:
            return []
        return resp.json() or []

    # ── Projekt-Board (core/projekt_git.py) ──

    async def _seiten(self, client: httpx.AsyncClient, token: str, pfad: str, params: dict, hoechstens: int) -> list[dict]:
        """Alle Seiten einer Liste bis ``hoechstens`` Einträge. Fehler → Ausnahme (nie still leer).

        404 heisst: Repo gibt es nicht — oder es ist privat und der Zugang fehlt."""
        ergebnis: list[dict] = []
        seite = 1
        while len(ergebnis) < hoechstens:
            status, daten = await _json_holen(client, f"{self.api_base}{pfad}", self._headers(token),
                                              {**params, "limit": SEITE, "page": seite})
            if status == 404:
                raise LookupError(pfad)
            if status in (401, 403):
                raise RuntimeError(f"{self.host} verweigert den Zugriff ({status}) — fehlt der Schlüssel?")
            if status != 200:
                raise RuntimeError(f"{self.host} {status} für {pfad}")
            if daten is None:
                daten = []
            if not isinstance(daten, list):
                raise RuntimeError(f"{self.host} liefert für {pfad} keine Liste")
            # Nur Eintraege, die wie ein Issue/PR aussehen — fremder Server.
            daten = [d for d in daten if isinstance(d, dict)]
            if not daten:
                break
            ergebnis.extend(daten)
            if len(daten) < SEITE:
                break
            seite += 1
        return ergebnis[:hoechstens]

    async def list_open_issues(self, client: httpx.AsyncClient, token: str, repo: str, hoechstens: int = 300) -> list[dict]:
        # type=issues: sonst stehen die Pull Requests mit in der Liste.
        rows = await self._seiten(client, token, f"/repos/{repo}/issues",
                                  {"state": "open", "type": "issues"}, hoechstens)
        return [r for r in rows if not r.get("pull_request")]

    async def list_open_pulls(self, client: httpx.AsyncClient, token: str, repo: str, hoechstens: int = 30) -> list[dict]:
        return await self._seiten(client, token, f"/repos/{repo}/pulls", {"state": "open"}, hoechstens)

    async def pull_state(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int) -> str | None:
        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}/pulls/{nummer}", self._headers(token))
        if status != 200 or not isinstance(daten, dict):
            return None
        if daten.get("merged") or daten.get("merged_at"):
            return "gemergt"
        return "geschlossen" if daten.get("state") == "closed" else "offen"

    async def issue_state(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int) -> str | None:
        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}/issues/{nummer}", self._headers(token))
        if status != 200 or not isinstance(daten, dict):
            return None
        return daten.get("state")

    async def checks_state(self, client: httpx.AsyncClient, token: str, repo: str, sha: str) -> str:
        """offen (keine Prüfungen) | ci_laeuft | ci_rot | ci_gruen — aus den Commit-Status."""
        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}/commits/{sha}/status",
                                          self._headers(token))
        if status != 200 or not isinstance(daten, dict):
            return "offen"
        if not daten.get("statuses"):
            return "offen"
        zustand = str(daten.get("state") or "")
        if zustand == "pending":
            return "ci_laeuft"
        if zustand in ("failure", "error"):
            return "ci_rot"
        return "ci_gruen" if zustand in ("success", "warning") else "offen"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        # Ohne Token: anonym (nur öffentliche Repos).
        kopf = {"Accept": "application/json"}
        if token:
            kopf["Authorization"] = f"token {token}"
        return kopf
