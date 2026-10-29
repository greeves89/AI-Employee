"""GitHub implementation of GitHostProvider.

Wraps the GitHub REST API with the exact URLs/headers/payloads that were
previously inlined in `agent_manager` and `self_test_service` (#532 phase 1)
— behaviour for GitHub is unchanged, only the seam moved.
"""

import httpx

from app.core.githost.base import GitHostProvider, kommentar_form


#: Was GitHub in ``x-accepted-github-permissions`` verlangt → wie es im
#: Token-Dialog heisst (fein granulierte Tokens, „Repository permissions“).
_RECHTE = {"issues": "Issues", "pull_requests": "Pull requests", "checks": "Checks",
           "statuses": "Commit statuses", "contents": "Contents", "metadata": "Metadata"}


def _zugriff_verweigert(resp: httpx.Response) -> str:
    """Verstaendliche Meldung zu 401/403: GitHubs eigener Grund plus das fehlende Recht.

    Ein fein granulierter Token sieht das Repo oft (Metadata), darf aber keine
    Issues lesen — „GitHub 403“ allein liess offen, was am Token fehlt.
    """
    try:
        grund = str((resp.json() or {}).get("message") or "").strip()
    except ValueError:
        grund = ""
    if resp.status_code == 401:
        return "GitHub lehnt den Schlüssel ab (ungültig oder abgelaufen)."
    if resp.headers.get("x-github-sso"):
        return ("Die Organisation verlangt eine SSO-Freigabe des Schlüssels — "
                "in GitHub beim Token „Configure SSO“ → Organisation autorisieren.")
    rechte = []
    for teil in (resp.headers.get("x-accepted-github-permissions") or "").split(";"):
        name, _, stufe = teil.strip().partition("=")
        if name and name in _RECHTE:
            rechte.append(f"{_RECHTE[name]}: {'Read' if stufe in ('read', '') else stufe}")
    if rechte:
        return (f"Dem Schlüssel fehlt das Recht {', '.join(rechte)} — in GitHub beim Token unter "
                f"„Repository permissions“ setzen.{f' (GitHub: {grund})' if grund else ''}")
    return f"GitHub verweigert den Zugriff{f': {grund}' if grund else ''}."


#: Abschluesse, die einen PR rot machen (Check-Runs wie Workflow-Laeufe).
_ROT = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}


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
        """Alle Seiten einer Liste bis ``hoechstens`` Einträge. Fehler → Ausnahme (nie still leer).

        404 heisst: Repo gibt es nicht — oder es ist privat und der Zugang fehlt."""
        ergebnis: list[dict] = []
        seite = 1
        while len(ergebnis) < hoechstens:
            resp = await client.get(
                f"{self.api_base}{pfad}", headers=self._headers(token),
                params={**params, "per_page": 100, "page": seite},
            )
            if resp.status_code == 404:
                raise LookupError(pfad)
            if resp.status_code in (401, 403) and resp.headers.get("x-ratelimit-remaining") == "0":
                raise RuntimeError("GitHub-Abfragegrenze erreicht — mit verbundenem GitHub-Zugang ist sie viel höher")
            if resp.status_code in (401, 403):
                raise RuntimeError(_zugriff_verweigert(resp))
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

    async def get_issue(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int) -> dict | None:
        """Ein Issue mit Tags und Zuständigen — None, wenn es nicht lesbar ist."""
        resp = await client.get(f"{self.api_base}/repos/{repo}/issues/{nummer}", headers=self._headers(token))
        return resp.json() if resp.status_code == 200 else None

    async def add_labels(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                         labels: list[str]) -> bool:
        resp = await client.post(f"{self.api_base}/repos/{repo}/issues/{nummer}/labels",
                                 headers=self._headers(token), json={"labels": labels})
        return resp.status_code in (200, 201)

    async def remove_label(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                           label: str) -> bool:
        from urllib.parse import quote

        resp = await client.delete(f"{self.api_base}/repos/{repo}/issues/{nummer}/labels/{quote(label, safe='')}",
                                   headers=self._headers(token))
        return resp.status_code in (200, 204, 404)

    async def remove_assignees(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                               logins: list[str]) -> bool:
        resp = await client.request("DELETE", f"{self.api_base}/repos/{repo}/issues/{nummer}/assignees",
                                    headers=self._headers(token), json={"assignees": logins})
        return resp.status_code in (200, 201)

    async def list_branches(self, client: httpx.AsyncClient, token: str, repo: str,
                            hoechstens: int = 300) -> list[dict]:
        """Branches als ``{"name", "sha"}`` — woran jemand von außen arbeitet."""
        rows = await self._seiten(client, token, f"/repos/{repo}/branches", {}, hoechstens)
        return [{"name": r.get("name"), "sha": (r.get("commit") or {}).get("sha")} for r in rows if r.get("name")]

    async def commit_zeit(self, client: httpx.AsyncClient, token: str, repo: str, sha: str) -> str | None:
        """Zeitpunkt eines Commits (ISO) — None, wenn nicht lesbar."""
        resp = await client.get(f"{self.api_base}/repos/{repo}/commits/{sha}", headers=self._headers(token))
        if resp.status_code != 200:
            return None
        commit = (resp.json() or {}).get("commit") or {}
        return (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")

    async def list_issue_comments(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                                  hoechstens: int = 300) -> list[dict]:
        """Kommentare eines Issues, älteste zuerst (``core/uebernahme`` liest die Übernahmen daraus)."""
        rows = await self._seiten(client, token, f"/repos/{repo}/issues/{nummer}/comments", {}, hoechstens)
        return [k for k in (kommentar_form(r) for r in rows) if k]

    # ── Abschnitt für die AGENTS.md als Pull Request (core/projekt_git.agents_md_pr) ──

    async def standard_branch(self, client: httpx.AsyncClient, token: str, repo: str) -> str | None:
        resp = await client.get(f"{self.api_base}/repos/{repo}", headers=self._headers(token))
        if resp.status_code != 200:
            raise RuntimeError(_zugriff_verweigert(resp) if resp.status_code in (401, 403)
                               else f"Repo {repo} nicht lesbar (GitHub {resp.status_code}).")
        return (resp.json() or {}).get("default_branch")

    async def datei_holen(self, client: httpx.AsyncClient, token: str, repo: str, pfad: str,
                          ref: str) -> tuple[str | None, str | None]:
        """(Inhalt, sha) einer Datei — (None, None), wenn es sie nicht gibt."""
        import base64

        resp = await client.get(f"{self.api_base}/repos/{repo}/contents/{pfad}", headers=self._headers(token),
                                params={"ref": ref})
        if resp.status_code == 404:
            return None, None
        if resp.status_code != 200:
            raise RuntimeError(_zugriff_verweigert(resp) if resp.status_code in (401, 403)
                               else f"{pfad} nicht lesbar (GitHub {resp.status_code}).")
        daten = resp.json() or {}
        roh = base64.b64decode(str(daten.get("content") or "")).decode("utf-8", errors="replace")
        return roh, daten.get("sha")

    async def branch_anlegen(self, client: httpx.AsyncClient, token: str, repo: str, neu: str, von: str) -> None:
        resp = await client.get(f"{self.api_base}/repos/{repo}/git/ref/heads/{von}", headers=self._headers(token))
        if resp.status_code != 200:
            raise RuntimeError(f"Branch {von} nicht lesbar (GitHub {resp.status_code}).")
        sha = ((resp.json() or {}).get("object") or {}).get("sha")
        resp = await client.post(f"{self.api_base}/repos/{repo}/git/refs", headers=self._headers(token),
                                 json={"ref": f"refs/heads/{neu}", "sha": sha})
        if resp.status_code == 422:
            return  # gibt es schon — die Datei wird darauf aktualisiert
        if resp.status_code not in (200, 201):
            raise RuntimeError(_zugriff_verweigert(resp) if resp.status_code in (401, 403, 404)
                               else f"Branch {neu} nicht angelegt (GitHub {resp.status_code}).")

    async def datei_schreiben(self, client: httpx.AsyncClient, token: str, repo: str, pfad: str, inhalt: str,
                              branch: str, nachricht: str, sha: str | None) -> None:
        import base64

        nutzlast = {"message": nachricht, "branch": branch,
                    "content": base64.b64encode(inhalt.encode("utf-8")).decode("ascii")}
        if sha:
            nutzlast["sha"] = sha
        resp = await client.put(f"{self.api_base}/repos/{repo}/contents/{pfad}", headers=self._headers(token),
                                json=nutzlast)
        if resp.status_code not in (200, 201):
            raise RuntimeError(_zugriff_verweigert(resp) if resp.status_code in (401, 403, 404)
                               else f"{pfad} nicht geschrieben (GitHub {resp.status_code}).")

    async def pull_anlegen(self, client: httpx.AsyncClient, token: str, repo: str, head: str, base: str,
                           titel: str, text: str) -> str | None:
        resp = await client.post(f"{self.api_base}/repos/{repo}/pulls", headers=self._headers(token),
                                 json={"title": titel, "head": head, "base": base, "body": text})
        if resp.status_code in (200, 201):
            return (resp.json() or {}).get("html_url")
        if resp.status_code == 422:
            # Es gibt schon einen offenen PR aus diesem Branch — der bekommt den neuen Stand.
            besitzer = repo.split("/", 1)[0]
            offen = await client.get(f"{self.api_base}/repos/{repo}/pulls", headers=self._headers(token),
                                     params={"head": f"{besitzer}:{head}", "state": "open"})
            if offen.status_code == 200 and offen.json():
                return offen.json()[0].get("html_url")
        raise RuntimeError(_zugriff_verweigert(resp) if resp.status_code in (401, 403, 404)
                           else f"Pull Request nicht angelegt (GitHub {resp.status_code}).")

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
        if resp.status_code in (401, 403):
            # Fein granulierte Tokens koennen Check-Runs nicht lesen (das Recht
            # „Checks“ gibt es nur fuer GitHub-Apps) — dann aus den Workflow-Laeufen
            # (Recht „Actions: Read“) und dem Commit-Status.
            return await self._ci_ohne_checks(client, token, repo, sha)
        if resp.status_code != 200:
            return "offen"
        laeufe = resp.json().get("check_runs") or []
        if not laeufe:
            return "offen"
        if any(r.get("status") != "completed" for r in laeufe):
            return "ci_laeuft"
        return "ci_rot" if any(r.get("conclusion") in _ROT for r in laeufe) else "ci_gruen"

    async def _ci_ohne_checks(self, client: httpx.AsyncClient, token: str, repo: str, sha: str) -> str:
        laeufe: list[dict] = []
        r = await client.get(f"{self.api_base}/repos/{repo}/actions/runs", headers=self._headers(token),
                             params={"head_sha": sha, "per_page": 50})
        if r.status_code == 200:
            laeufe = [x for x in (r.json() or {}).get("workflow_runs") or [] if isinstance(x, dict)]
        zustand, status = "", []
        r = await client.get(f"{self.api_base}/repos/{repo}/commits/{sha}/status", headers=self._headers(token))
        if r.status_code == 200:
            daten = r.json() or {}
            zustand, status = str(daten.get("state") or ""), daten.get("statuses") or []
        if not laeufe and not status:
            return "offen"
        if any(x.get("status") != "completed" for x in laeufe) or (status and zustand == "pending"):
            return "ci_laeuft"
        if any(x.get("conclusion") in _ROT for x in laeufe) or (status and zustand in ("failure", "error")):
            return "ci_rot"
        return "ci_gruen"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        # Ohne Token: anonym (nur öffentliche Repos, niedrige Abfragegrenze).
        kopf = {"Accept": "application/vnd.github+json"}
        if token:
            kopf["Authorization"] = f"Bearer {token}"
        return kopf
