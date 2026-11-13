"""GitLab implementation of GitHostProvider (#1194) — gitlab.com and self-hosted.

GitLab speaks REST under ``<base>/api/v4``. The project is addressed by its full
path (``group/sub/name``, URL-encoded as one segment), issues and merge requests
by their per-project ``iid``. The board (``core/projekt_git``) reads GitHub-shaped
dicts — ``number``, ``title``, ``body``, ``html_url``, ``labels[].name``,
``assignees[].login``, ``head.sha``/``head.ref`` — so this adapter maps every
GitLab object into that shape (``_issue``, ``_mr``). Everything else in the board
(columns from labels, take-over header in comments, return channel, push lock)
runs unchanged for both providers.

Differences that matter here:

* a token goes in ``PRIVATE-TOKEN`` (personal, project or group access token);
* issue state is ``opened``/``closed`` (mapped to ``open``/``closed``), a merge
  request is ``merged`` instead of carrying ``merged_at`` alone;
* labels are set and removed by name on the issue (``add_labels``/``remove_labels``),
  GitLab creates missing labels itself;
* CI is the latest **pipeline** for the commit;
* comments are "notes"; system notes (label changes etc.) are skipped, and the
  role of an author comes from the project membership (Developer or higher =
  trusted, like GitHub's OWNER/MEMBER/COLLABORATOR).

The server is entered freely by a user: no redirects are followed (the token
would go to a target nobody checked), answers are size-limited, and links in
answers are only accepted on the same server (``projekt_git._sichere_url``).
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from app.core.githost.base import GitHostProvider, kommentar_form
from app.core.githost.forgejo import _json_holen

SEITE = 100
#: Ab dieser Rolle (Developer) darf jemand ins Repo schreiben — dessen Kommentare zählen.
SCHREIBRECHT_AB = 30
#: Pipeline-Zustände (GitLab) → CI-Stand des Boards.
_LAEUFT = {"created", "waiting_for_resource", "preparing", "pending", "running", "scheduled"}
_ROT = {"failed", "canceled"}


def _ganzzahl(wert) -> int | None:
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


def _person(roh) -> dict | None:
    if not isinstance(roh, dict) or not isinstance(roh.get("username"), str):
        return None
    return {"login": roh["username"][:100], "id": _ganzzahl(roh.get("id"))}


def _issue(roh: dict) -> dict:
    """GitLab-Issue → Form, die das Board liest (wie GitHub)."""
    zustand = roh.get("state")
    return {
        "number": _ganzzahl(roh.get("iid")),
        "title": roh.get("title"),
        "body": roh.get("description"),
        "html_url": roh.get("web_url"),
        "labels": [{"name": n} for n in roh.get("labels") or [] if isinstance(n, str)],
        "assignees": [p for p in (_person(a) for a in roh.get("assignees") or []) if p],
        "state": "open" if zustand == "opened" else zustand,
        "updated_at": roh.get("updated_at"),
        "created_at": roh.get("created_at"),
        "comments": _ganzzahl(roh.get("user_notes_count")),
    }


def _mr(roh: dict) -> dict:
    """GitLab-Merge-Request → Form eines Pull Requests."""
    return {
        "number": _ganzzahl(roh.get("iid")),
        "title": roh.get("title"),
        "body": roh.get("description"),
        "html_url": roh.get("web_url"),
        "labels": [{"name": n} for n in roh.get("labels") or [] if isinstance(n, str)],
        "head": {"sha": roh.get("sha") if isinstance(roh.get("sha"), str) else None,
                 "ref": roh.get("source_branch") if isinstance(roh.get("source_branch"), str) else None},
        "state": roh.get("state"),
    }


class GitLabHostProvider(GitHostProvider):
    def __init__(self, base_url: str = "https://gitlab.com"):
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v4"
        self.host = self.base_url.split("://", 1)[-1]
        self.anzeigename = self.host
        self.art = "gitlab"

    # ── Agenten-Zugang ──

    def egress_ziele(self) -> list[str]:
        return [self.host.split("/", 1)[0].split(":", 1)[0].lower()]

    def get_agent_env(self, token: str) -> dict[str, str]:
        # `glab` liest GITLAB_TOKEN und GITLAB_HOST; curl-Aufrufe nehmen GITLAB_TOKEN.
        return {"GITLAB_TOKEN": token, "GITLAB_HOST": self.base_url}

    # ── Grundbausteine ──

    def _p(self, repo: str) -> str:
        """Projekt-Kennung: der ganze Pfad als ein Segment."""
        return f"{self.api_base}/projects/{quote(repo, safe='')}"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        kopf = {"Accept": "application/json"}
        if token:
            kopf["PRIVATE-TOKEN"] = token
        return kopf

    def _abgelehnt(self, status: int, was: str) -> RuntimeError:
        if status in (401, 403):
            return RuntimeError(f"{self.host} verweigert {was} ({status}) — fehlt dem Token das Recht api "
                                "oder die Rolle Developer?")
        return RuntimeError(f"{self.host}: {was} gescheitert ({status}).")

    async def _schreiben(self, client: httpx.AsyncClient, methode: str, url: str, token: str,
                         json: dict | None = None, params: dict | None = None) -> httpx.Response:
        return await client.request(methode, url, headers=self._headers(token), json=json, params=params,
                                    follow_redirects=False)

    async def _eins(self, client: httpx.AsyncClient, token: str, url: str, params: dict | None = None):
        status, daten = await _json_holen(client, url, self._headers(token), params)
        return status, (daten if isinstance(daten, dict) else None)

    async def _seiten(self, client: httpx.AsyncClient, token: str, url: str, params: dict,
                      hoechstens: int) -> list[dict]:
        """Alle Seiten bis ``hoechstens`` Einträge. Fehler → Ausnahme (nie still leer)."""
        ergebnis: list[dict] = []
        seite = 1
        while len(ergebnis) < hoechstens:
            status, daten = await _json_holen(client, url, self._headers(token),
                                              {**params, "per_page": SEITE, "page": seite})
            if status == 404:
                raise LookupError(url)
            if status in (401, 403):
                raise self._abgelehnt(status, "den Zugriff")
            if status != 200:
                raise RuntimeError(f"{self.host} {status}")
            if daten is None:
                daten = []
            if not isinstance(daten, list):
                raise RuntimeError(f"{self.host} liefert keine Liste")
            daten = [d for d in daten if isinstance(d, dict)]
            if not daten:
                break
            ergebnis.extend(daten)
            if len(daten) < SEITE:
                break
            seite += 1
        return ergebnis[:hoechstens]

    # ── GitHostProvider (Selbsttest, Meldungen) ──

    async def search_open_issue(self, client, token, repo, title):
        try:
            rows = await self._seiten(client, token, f"{self._p(repo)}/issues",
                                      {"state": "opened", "search": title, "in": "title"}, SEITE)
        except (LookupError, RuntimeError):
            return None
        for r in rows:
            if r.get("title") == title:
                return _ganzzahl(r.get("iid"))
        return None

    async def create_issue(self, client, token, repo, title, body, labels):
        nutzlast = {"title": title, "description": body}
        sauber = [lbl for lbl in labels or [] if isinstance(lbl, str) and "," not in lbl]
        if sauber:
            nutzlast["labels"] = ",".join(sauber)
        resp = await self._schreiben(client, "POST", f"{self._p(repo)}/issues", token, nutzlast)
        if resp.status_code in (200, 201):
            return (resp.json() or {}).get("web_url")
        return None

    async def comment_issue(self, client, token, repo, issue_number, body):
        resp = await self._schreiben(client, "POST", f"{self._p(repo)}/issues/{int(issue_number)}/notes", token,
                                     {"body": body})
        if resp.status_code not in (200, 201):
            raise self._abgelehnt(resp.status_code, "den Kommentar")

    async def close_issue(self, client, token, repo, issue_number):
        await self._schreiben(client, "PUT", f"{self._p(repo)}/issues/{int(issue_number)}", token,
                              {"state_event": "close"})

    async def list_open_issues_with_label(self, client, token, repo, label):
        try:
            rows = await self._seiten(client, token, f"{self._p(repo)}/issues",
                                      {"state": "opened", "labels": label}, SEITE)
        except (LookupError, RuntimeError):
            return []
        return [_issue(r) for r in rows]

    # ── Projekt-Board (core/projekt_git.py) ──

    async def list_open_issues(self, client, token, repo, hoechstens: int = 300) -> list[dict]:
        rows = await self._seiten(client, token, f"{self._p(repo)}/issues", {"state": "opened"}, hoechstens)
        return [_issue(r) for r in rows]

    async def list_open_pulls(self, client, token, repo, hoechstens: int = 30) -> list[dict]:
        rows = await self._seiten(client, token, f"{self._p(repo)}/merge_requests", {"state": "opened"},
                                  hoechstens)
        return [_mr(r) for r in rows]

    async def pull_state(self, client, token, repo, nummer: int) -> str | None:
        status, daten = await self._eins(client, token, f"{self._p(repo)}/merge_requests/{int(nummer)}")
        if status != 200 or daten is None:
            return None
        zustand = daten.get("state")
        if zustand == "merged" or daten.get("merged_at"):
            return "gemergt"
        return "geschlossen" if zustand in ("closed", "locked") else "offen"

    async def get_issue(self, client, token, repo, nummer: int) -> dict | None:
        status, daten = await self._eins(client, token, f"{self._p(repo)}/issues/{int(nummer)}")
        return _issue(daten) if status == 200 and daten is not None else None

    async def issue_state(self, client, token, repo, nummer: int) -> str | None:
        issue = await self.get_issue(client, token, repo, nummer)
        return issue.get("state") if issue else None

    async def list_labels(self, client, token, repo, hoechstens: int = 300) -> list[str]:
        rows = await self._seiten(client, token, f"{self._p(repo)}/labels", {}, hoechstens)
        return [str(r["name"]) for r in rows if isinstance(r.get("name"), str)]

    async def add_labels(self, client, token, repo, nummer: int, labels: list[str]) -> bool:
        # Kommas trennen in GitLab Labels — ein Name mit Komma würde zu zwei Labels.
        sauber = [lbl for lbl in labels if isinstance(lbl, str) and lbl and "," not in lbl]
        if not sauber:
            return False
        resp = await self._schreiben(client, "PUT", f"{self._p(repo)}/issues/{int(nummer)}", token,
                                     {"add_labels": ",".join(sauber)})
        return resp.status_code == 200

    async def remove_label(self, client, token, repo, nummer: int, label: str) -> bool:
        if not label or "," in label:
            return False
        resp = await self._schreiben(client, "PUT", f"{self._p(repo)}/issues/{int(nummer)}", token,
                                     {"remove_labels": label})
        return resp.status_code in (200, 404)

    async def remove_assignees(self, client, token, repo, nummer: int, logins: list[str]) -> bool:
        # GitLab setzt die Zuständigen als Ganzes (Kennungen): die übrigen behalten.
        issue = await self.get_issue(client, token, repo, nummer)
        if issue is None:
            return False
        weg = {login.lower() for login in logins}
        bleiben = [p["id"] for p in issue.get("assignees") or []
                   if p.get("id") is not None and p["login"].lower() not in weg]
        resp = await self._schreiben(client, "PUT", f"{self._p(repo)}/issues/{int(nummer)}", token,
                                     {"assignee_ids": bleiben})
        return resp.status_code == 200

    async def list_branches(self, client, token, repo, hoechstens: int = 300) -> list[dict]:
        rows = await self._seiten(client, token, f"{self._p(repo)}/repository/branches", {}, hoechstens)
        return [{"name": r.get("name"), "sha": (r.get("commit") or {}).get("id"),
                 "zeit": (r.get("commit") or {}).get("committed_date")} for r in rows if r.get("name")]

    async def _commit(self, client, token, repo, sha: str) -> dict | None:
        status, daten = await self._eins(client, token,
                                         f"{self._p(repo)}/repository/commits/{quote(str(sha), safe='')}")
        return daten if status == 200 else None

    async def commit_zeit(self, client, token, repo, sha: str) -> str | None:
        daten = await self._commit(client, token, repo, sha)
        return (daten.get("committed_date") or daten.get("created_at")) if daten else None

    async def commit_info(self, client, token, repo, sha: str) -> dict | None:
        daten = await self._commit(client, token, repo, sha)
        if not daten:
            return None
        return {"zeit": daten.get("committed_date") or daten.get("created_at"),
                "autor_name": str(daten.get("author_name") or "")[:100],
                "autor_mail": str(daten.get("author_email") or "")[:200]}

    async def kommentar_aendern(self, client, token, repo, kommentar_id: int, body: str, *,
                                nummer: int | None = None) -> bool:
        """Kommentar ersetzen — GitLab braucht dazu auch das Issue (``nummer``)."""
        if nummer is None:
            return False
        resp = await self._schreiben(client, "PUT",
                                     f"{self._p(repo)}/issues/{int(nummer)}/notes/{int(kommentar_id)}", token,
                                     {"body": body})
        return resp.status_code == 200

    async def _schreibrecht(self, client, token, repo, nutzer_id: int) -> bool:
        status, daten = await self._eins(client, token, f"{self._p(repo)}/members/all/{int(nutzer_id)}")
        if status != 200 or daten is None:
            return False
        stufe = _ganzzahl(daten.get("access_level"))
        return stufe is not None and stufe >= SCHREIBRECHT_AB

    async def list_issue_comments(self, client, token, repo, nummer: int, hoechstens: int = 300) -> list[dict]:
        """Kommentare (Notes) eines Issues, älteste zuerst, ohne Systemeinträge."""
        rows = await self._seiten(client, token, f"{self._p(repo)}/issues/{int(nummer)}/notes",
                                  {"sort": "asc", "order_by": "created_at"}, hoechstens)
        kommentare: list[dict] = []
        geprueft: dict[int, bool] = {}
        for r in rows:
            if r.get("system"):
                continue
            autor = r.get("author") if isinstance(r.get("author"), dict) else {}
            k = kommentar_form({"id": r.get("id"), "body": r.get("body"),
                                "user": {"login": autor.get("username")},
                                "created_at": r.get("created_at"), "updated_at": r.get("updated_at")})
            if not k:
                continue
            # Vertraut ist, wer im Projekt mindestens Developer ist — je Autor eine
            # Abfrage, höchstens zehn je Issue.
            aid = _ganzzahl(autor.get("id"))
            if aid is not None and token:
                if aid not in geprueft:
                    geprueft[aid] = len(geprueft) < 10 and await self._schreibrecht(client, token, repo, aid)
                k["vertraut"] = geprueft[aid]
            kommentare.append(k)
        return kommentare

    async def repo_oeffentlich(self, client, token, repo) -> bool | None:
        status, daten = await self._eins(client, token, self._p(repo))
        if status != 200 or daten is None or "visibility" not in daten:
            return None
        return daten.get("visibility") == "public"

    async def eigenes_konto(self, client, token) -> str | None:
        if not token:
            return None
        status, daten = await self._eins(client, token, f"{self.api_base}/user")
        return str(daten.get("username") or "") or None if status == 200 and daten else None

    # ── Abschnitt für die AGENTS.md als Merge Request (core/projekt_git.agents_md_pr) ──

    async def standard_branch(self, client, token, repo) -> str | None:
        status, daten = await self._eins(client, token, self._p(repo))
        if status != 200 or daten is None:
            raise self._abgelehnt(status, "das Lesen des Projekts")
        return daten.get("default_branch")

    async def datei_holen(self, client, token, repo, pfad: str, ref: str) -> tuple[str | None, str | None]:
        import base64

        status, daten = await self._eins(client, token,
                                         f"{self._p(repo)}/repository/files/{quote(pfad, safe='')}", {"ref": ref})
        if status == 404:
            return None, None
        if status != 200 or daten is None:
            raise self._abgelehnt(status, f"das Lesen von {pfad}")
        roh = base64.b64decode(str(daten.get("content") or "")).decode("utf-8", errors="replace")
        # Kein sha nötig zum Überschreiben — die Kennung zeigt nur: die Datei gibt es.
        return roh, str(daten.get("blob_id") or "vorhanden")

    async def branch_anlegen(self, client, token, repo, neu: str, von: str) -> None:
        resp = await self._schreiben(client, "POST", f"{self._p(repo)}/repository/branches", token,
                                     params={"branch": neu, "ref": von})
        if resp.status_code in (200, 201):
            return
        if resp.status_code == 400 and "already exists" in resp.text:
            return  # gibt es schon — die Datei wird darauf aktualisiert
        raise self._abgelehnt(resp.status_code, f"das Anlegen von {neu}")

    async def datei_schreiben(self, client, token, repo, pfad: str, inhalt: str, branch: str, nachricht: str,
                              sha: str | None) -> None:
        resp = await self._schreiben(client, "PUT" if sha else "POST",
                                     f"{self._p(repo)}/repository/files/{quote(pfad, safe='')}", token,
                                     {"branch": branch, "content": inhalt, "commit_message": nachricht,
                                      "encoding": "text"})
        if resp.status_code not in (200, 201):
            raise self._abgelehnt(resp.status_code, f"das Schreiben von {pfad}")

    async def pull_anlegen(self, client, token, repo, head: str, base: str, titel: str, text: str) -> str | None:
        """Merge Request anlegen — gibt es schon einen offenen aus ``head``, dessen Adresse."""
        resp = await self._schreiben(client, "POST", f"{self._p(repo)}/merge_requests", token,
                                     {"source_branch": head, "target_branch": base, "title": titel,
                                      "description": text, "remove_source_branch": True})
        if resp.status_code in (200, 201):
            return (resp.json() or {}).get("web_url")
        if resp.status_code == 409:
            status, offen = await _json_holen(client, f"{self._p(repo)}/merge_requests", self._headers(token),
                                              {"state": "opened", "source_branch": head})
            for mr in offen if status == 200 and isinstance(offen, list) else []:
                if isinstance(mr, dict) and mr.get("source_branch") == head:
                    return mr.get("web_url")
        raise self._abgelehnt(resp.status_code, "das Anlegen des Merge Requests")

    async def checks_state(self, client, token, repo, sha: str) -> str:
        """offen (keine Pipeline) | ci_laeuft | ci_rot | ci_gruen — aus der jüngsten Pipeline."""
        status, daten = await _json_holen(client, f"{self._p(repo)}/pipelines", self._headers(token),
                                          {"sha": sha, "per_page": 5, "order_by": "id", "sort": "desc"})
        if status != 200 or not isinstance(daten, list):
            return "offen"
        pipelines = [p for p in daten if isinstance(p, dict)]
        if not pipelines:
            return "offen"
        zustand = str(pipelines[0].get("status") or "")
        if zustand in _LAEUFT:
            return "ci_laeuft"
        if zustand in _ROT:
            return "ci_rot"
        return "ci_gruen" if zustand == "success" else "offen"
