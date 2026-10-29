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

from app.core.githost.base import GitHostProvider, kommentar_form

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

    async def get_issue(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int) -> dict | None:
        """Ein Issue mit Tags und Zuständigen — None, wenn es nicht lesbar ist."""
        resp = await client.get(f"{self.api_base}/repos/{repo}/issues/{nummer}", headers=self._headers(token))
        return resp.json() if resp.status_code == 200 else None

    async def _label_id(self, client: httpx.AsyncClient, token: str, repo: str, name: str) -> int | None:
        """Forgejo/Gitea vergeben Tags per Kennung: vorhandenes suchen, sonst anlegen."""
        resp = await client.get(f"{self.api_base}/repos/{repo}/labels", headers=self._headers(token),
                                params={"limit": 50})
        if resp.status_code == 200:
            for eintrag in resp.json() or []:
                if isinstance(eintrag, dict) and eintrag.get("name") == name:
                    return eintrag.get("id")
        neu = await client.post(f"{self.api_base}/repos/{repo}/labels", headers=self._headers(token),
                                json={"name": name, "color": "#fbca04"})
        return neu.json().get("id") if neu.status_code in (200, 201) else None

    async def add_labels(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                         labels: list[str]) -> bool:
        ids = [i for i in [await self._label_id(client, token, repo, n) for n in labels] if i is not None]
        if not ids:
            return False
        resp = await client.post(f"{self.api_base}/repos/{repo}/issues/{nummer}/labels",
                                 headers=self._headers(token), json={"labels": ids})
        return resp.status_code in (200, 201)

    async def remove_label(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                           label: str) -> bool:
        lid = await self._label_id(client, token, repo, label)
        if lid is None:
            return True
        resp = await client.delete(f"{self.api_base}/repos/{repo}/issues/{nummer}/labels/{lid}",
                                   headers=self._headers(token))
        return resp.status_code in (200, 204, 404)

    async def remove_assignees(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                               logins: list[str]) -> bool:
        # Forgejo/Gitea setzen die Zuständigen als Ganzes: die übrigen behalten.
        issue = await self.get_issue(client, token, repo, nummer)
        if not isinstance(issue, dict):
            return False
        weg = {login.lower() for login in logins}
        bleiben = [p.get("login") for p in issue.get("assignees") or []
                   if isinstance(p, dict) and p.get("login") and p["login"].lower() not in weg]
        resp = await client.patch(f"{self.api_base}/repos/{repo}/issues/{nummer}",
                                  headers=self._headers(token), json={"assignees": bleiben})
        return resp.status_code in (200, 201)

    async def list_branches(self, client: httpx.AsyncClient, token: str, repo: str,
                            hoechstens: int = 300) -> list[dict]:
        """Branches als ``{"name", "sha", "zeit"}`` — Forgejo nennt den Zeitpunkt gleich mit."""
        rows = await self._seiten(client, token, f"/repos/{repo}/branches", {}, hoechstens)
        return [{"name": r.get("name"), "sha": (r.get("commit") or {}).get("id"),
                 "zeit": (r.get("commit") or {}).get("timestamp")} for r in rows if r.get("name")]

    async def commit_zeit(self, client: httpx.AsyncClient, token: str, repo: str, sha: str) -> str | None:
        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}/git/commits/{sha}",
                                          self._headers(token))
        if status != 200 or not isinstance(daten, dict):
            return None
        commit = daten.get("commit") or {}
        return (commit.get("committer") or {}).get("date") or daten.get("created")

    async def commit_info(self, client: httpx.AsyncClient, token: str, repo: str, sha: str) -> dict | None:
        """Zeit und Autor eines Commits — ``{"zeit", "autor_name", "autor_mail"}`` oder None."""
        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}/git/commits/{sha}",
                                          self._headers(token))
        if status != 200 or not isinstance(daten, dict):
            return None
        commit = daten.get("commit") or {}
        autor = commit.get("author") or {}
        return {"zeit": (commit.get("committer") or {}).get("date") or daten.get("created"),
                "autor_name": str(autor.get("name") or "")[:100], "autor_mail": str(autor.get("email") or "")[:200]}

    async def kommentar_aendern(self, client: httpx.AsyncClient, token: str, repo: str, kommentar_id: int,
                                body: str) -> bool:
        """Einen Issue-Kommentar ersetzen (Übernahme verlängern)."""
        resp = await client.patch(f"{self.api_base}/repos/{repo}/issues/comments/{kommentar_id}",
                                  headers=self._headers(token), json={"body": body}, follow_redirects=False)
        return resp.status_code in (200, 201)

    async def list_issue_comments(self, client: httpx.AsyncClient, token: str, repo: str, nummer: int,
                                  hoechstens: int = 300) -> list[dict]:
        """Kommentare eines Issues, älteste zuerst (``core/uebernahme`` liest die Übernahmen daraus)."""
        rows = await self._seiten(client, token, f"/repos/{repo}/issues/{nummer}/comments", {}, hoechstens)
        kommentare = [k for k in (kommentar_form(r) for r in rows) if k]
        # Forgejo/Gitea nennen am Kommentar keine Rolle: Eigentümer des Repos oder
        # Mitarbeiter (je Autor eine Abfrage, höchstens zehn) gelten als vertraut.
        besitzer = repo.split("/", 1)[0].lower()
        geprueft: dict[str, bool] = {}
        for k in kommentare:
            autor = (k.get("autor") or "").lower()
            if not autor:
                continue
            if autor not in geprueft:
                if autor == besitzer:
                    geprueft[autor] = True
                elif len(geprueft) < 10 and token:
                    from urllib.parse import quote

                    resp = await client.get(f"{self.api_base}/repos/{repo}/collaborators/{quote(k['autor'], safe='')}",
                                            headers=self._headers(token), follow_redirects=False)
                    geprueft[autor] = resp.status_code == 204
                else:
                    geprueft[autor] = False
            k["vertraut"] = geprueft[autor]
        return kommentare

    async def eigenes_konto(self, client: httpx.AsyncClient, token: str) -> str | None:
        """Login des Kontos hinter dem Schlüssel — None ohne Schlüssel oder wenn nicht lesbar."""
        if not token:
            return None
        status, daten = await _json_holen(client, f"{self.api_base}/user", self._headers(token))
        return str(daten.get("login") or "") or None if status == 200 and isinstance(daten, dict) else None

    # ── Abschnitt für die AGENTS.md als Pull Request (core/projekt_git.agents_md_pr) ──

    def _abgelehnt(self, status: int, was: str) -> RuntimeError:
        if status in (401, 403):
            return RuntimeError(f"{self.host} verweigert {was} ({status}) — darf der Schlüssel ins Repo schreiben?")
        return RuntimeError(f"{self.host}: {was} gescheitert ({status}).")

    async def standard_branch(self, client: httpx.AsyncClient, token: str, repo: str) -> str | None:
        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}", self._headers(token))
        if status != 200 or not isinstance(daten, dict):
            raise self._abgelehnt(status, "das Lesen des Repos")
        return daten.get("default_branch")

    async def datei_holen(self, client: httpx.AsyncClient, token: str, repo: str, pfad: str,
                          ref: str) -> tuple[str | None, str | None]:
        import base64

        status, daten = await _json_holen(client, f"{self.api_base}/repos/{repo}/contents/{pfad}",
                                          self._headers(token), {"ref": ref})
        if status == 404:
            return None, None
        if status != 200 or not isinstance(daten, dict):
            raise self._abgelehnt(status, f"das Lesen von {pfad}")
        roh = base64.b64decode(str(daten.get("content") or "")).decode("utf-8", errors="replace")
        return roh, daten.get("sha")

    async def branch_anlegen(self, client: httpx.AsyncClient, token: str, repo: str, neu: str, von: str) -> None:
        resp = await client.post(f"{self.api_base}/repos/{repo}/branches", headers=self._headers(token),
                                 json={"new_branch_name": neu, "old_branch_name": von}, follow_redirects=False)
        if resp.status_code in (200, 201, 409):
            return  # 409: gibt es schon — die Datei wird darauf aktualisiert
        raise self._abgelehnt(resp.status_code, f"das Anlegen von {neu}")

    async def datei_schreiben(self, client: httpx.AsyncClient, token: str, repo: str, pfad: str, inhalt: str,
                              branch: str, nachricht: str, sha: str | None) -> None:
        import base64

        nutzlast = {"message": nachricht, "branch": branch,
                    "content": base64.b64encode(inhalt.encode("utf-8")).decode("ascii")}
        if sha:
            nutzlast["sha"] = sha
        resp = await client.request("PUT" if sha else "POST", f"{self.api_base}/repos/{repo}/contents/{pfad}",
                                    headers=self._headers(token), json=nutzlast, follow_redirects=False)
        if resp.status_code not in (200, 201):
            raise self._abgelehnt(resp.status_code, f"das Schreiben von {pfad}")

    async def pull_anlegen(self, client: httpx.AsyncClient, token: str, repo: str, head: str, base: str,
                           titel: str, text: str) -> str | None:
        resp = await client.post(f"{self.api_base}/repos/{repo}/pulls", headers=self._headers(token),
                                 json={"title": titel, "head": head, "base": base, "body": text},
                                 follow_redirects=False)
        if resp.status_code in (200, 201):
            return resp.json().get("html_url")
        if resp.status_code == 409:
            status, offen = await _json_holen(client, f"{self.api_base}/repos/{repo}/pulls", self._headers(token),
                                              {"state": "open", "limit": SEITE})
            for pr in offen if status == 200 and isinstance(offen, list) else []:
                if isinstance(pr, dict) and (pr.get("head") or {}).get("ref") == head:
                    return pr.get("html_url")
        raise self._abgelehnt(resp.status_code, "das Anlegen des Pull Requests")

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
