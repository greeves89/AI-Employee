"""Projekte mit GitLab-Repos (#1194) — gitlab.com und selbst betrieben.

GitLab ist ein Anbieter wie GitHub und Forgejo: ``githost/gitlab.py`` bildet
Issues, Merge Requests und Notes auf die Form ab, die das Board liest; der
Abgleich (``core/projekt_git``) ist für alle derselbe. Der Server wird mit
``httpx.MockTransport`` nachgestellt — Pfade, Kopfzeilen, Nutzlasten und die
Abbildung werden wirklich geprüft.

Sicherheit: Der Token geht nur an den eingetragenen Server (keine Weiterleitung),
steht in keiner Meldung, keinem Protokoll und keinem Auftrag an den Agenten;
interne Adressen nur nach Freigabe durch den Administrator; Repo und Anbieter
ändert nur, wer das Projekt angelegt hat.
"""

import json
import logging
import re
import ssl
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.api import teams as api
from app.core import egress, projekt_git, projekte
from app.core import uebernahme as u
from app.core.githost.gitlab import GitLabHostProvider
from app.core.githost.registry import get_git_host_provider
from app.models.projekt_karte import ProjektKarte
from app.models.projekt_nachricht import ProjektNachricht
from app.models.team import Team
from tests.test_projekte import MITGLIED, Basis

BASIS = "https://gitlab.example.com"
REPO = "team/sub/repo"
ROH = "team%2Fsub%2Frepo"
TOKEN = "glpat-SENTINEL-1194-geheim"
ANLAGE = "anlage-test"


def _jetzt(minuten: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minuten)).isoformat(timespec="seconds")


class GitLab:
    """Ein kleines GitLab: Issues, Merge Requests, Notes, Labels, Mitglieder, Pipelines."""

    def __init__(self):
        self.issues: dict[int, dict] = {}
        self.mrs: dict[int, dict] = {}
        self.notes: dict[int, list[dict]] = {}
        self.labels = ["bug"]
        self.members = {7: 30, 8: 10}          # Nutzer-ID → Rolle (30 = Developer)
        self.pipelines: dict[str, str] = {}
        self.anfragen: list[httpx.Request] = []
        self.naechste_note = 500

    def issue(self, iid, titel="Fehler", labels=(), **kw):
        self.issues[iid] = {"iid": iid, "id": 9000 + iid, "title": titel, "description": "Text", "state": "opened",
                            "web_url": f"{BASIS}/{REPO}/-/issues/{iid}", "labels": list(labels),
                            "assignees": [], "updated_at": _jetzt(-5), "created_at": _jetzt(-60),
                            "user_notes_count": 0, **kw}
        return self.issues[iid]

    def note(self, iid, body, autor_id=7, autor="dev-bot", system=False, erstellt=None):
        self.naechste_note += 1
        self.notes.setdefault(iid, []).append({
            "id": self.naechste_note, "body": body, "system": system, "created_at": erstellt or _jetzt(1),
            "updated_at": erstellt or _jetzt(1), "author": {"id": autor_id, "username": autor}})
        self.issues[iid]["user_notes_count"] = len(self.notes[iid])
        self.issues[iid]["updated_at"] = _jetzt(1)

    def transport(self):
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.anfragen.append(request)
        pfad = request.url.raw_path.decode().split("?", 1)[0]
        p = f"/api/v4/projects/{ROH}"
        params = request.url.params
        daten = json.loads(request.content) if request.content else {}
        if pfad == "/api/v4/user":
            return httpx.Response(200, json={"username": "board-bot"})
        if not pfad.startswith(p):
            return httpx.Response(404, json={"message": "404 Project Not Found"})
        rest = pfad[len(p):]
        if rest == "" and request.method == "GET":
            return httpx.Response(200, json={"visibility": "private", "default_branch": "main"})
        if rest == "/issues" and request.method == "GET":
            offen = [i for i in self.issues.values() if i["state"] == "opened"]
            seite, je = int(params.get("page", "1")), int(params.get("per_page", "20"))
            return httpx.Response(200, json=offen[(seite - 1) * je: seite * je])
        if m := re.fullmatch(r"/issues/(\d+)", rest):
            issue = self.issues.get(int(m.group(1)))
            if issue is None:
                return httpx.Response(404, json={})
            if request.method == "PUT":
                for name in filter(None, (daten.get("add_labels") or "").split(",")):
                    if name not in issue["labels"]:
                        issue["labels"].append(name)
                for name in filter(None, (daten.get("remove_labels") or "").split(",")):
                    issue["labels"] = [x for x in issue["labels"] if x != name]
                if "assignee_ids" in daten:
                    issue["assignees"] = [a for a in issue["assignees"] if a["id"] in daten["assignee_ids"]]
            return httpx.Response(200, json=issue)
        if m := re.fullmatch(r"/issues/(\d+)/notes", rest):
            iid = int(m.group(1))
            if request.method == "POST":
                self.note(iid, daten["body"], autor_id=99, autor="board-bot")
                return httpx.Response(201, json=self.notes[iid][-1])
            return httpx.Response(200, json=self.notes.get(iid, []))
        if m := re.fullmatch(r"/issues/(\d+)/notes/(\d+)", rest):
            for n in self.notes.get(int(m.group(1)), []):
                if n["id"] == int(m.group(2)):
                    n["body"] = daten["body"]
                    return httpx.Response(200, json=n)
            return httpx.Response(404, json={})
        if m := re.fullmatch(r"/members/all/(\d+)", rest):
            stufe = self.members.get(int(m.group(1)))
            return httpx.Response(200, json={"access_level": stufe}) if stufe else httpx.Response(404, json={})
        if rest == "/labels":
            return httpx.Response(200, json=[{"name": n} for n in self.labels])
        if rest == "/merge_requests" and request.method == "GET":
            offen = [mr for mr in self.mrs.values() if mr["state"] == "opened"
                     and (not params.get("source_branch") or mr["source_branch"] == params["source_branch"])]
            return httpx.Response(200, json=offen)
        if rest == "/merge_requests" and request.method == "POST":
            if any(mr["source_branch"] == daten["source_branch"] for mr in self.mrs.values()):
                return httpx.Response(409, json={"message": ["Another open merge request already exists"]})
            iid = len(self.mrs) + 1
            self.mrs[iid] = {"iid": iid, "title": daten["title"], "description": daten["description"],
                             "state": "opened", "source_branch": daten["source_branch"], "sha": "neu",
                             "web_url": f"{BASIS}/{REPO}/-/merge_requests/{iid}", "labels": []}
            return httpx.Response(201, json=self.mrs[iid])
        if m := re.fullmatch(r"/merge_requests/(\d+)", rest):
            mr = self.mrs.get(int(m.group(1)))
            return httpx.Response(200, json=mr) if mr else httpx.Response(404, json={})
        if rest == "/pipelines":
            zustand = self.pipelines.get(params.get("sha", ""))
            return httpx.Response(200, json=[{"id": 1, "status": zustand}] if zustand else [])
        if rest.startswith("/repository/files/"):
            if request.method == "GET":
                return httpx.Response(404, json={"message": "404 File Not Found"})
            self.dateien = getattr(self, "dateien", []) + [(request.method, daten)]
            return httpx.Response(201, json={"file_path": "AGENTS.md"})
        if rest == "/repository/branches" and request.method == "POST":
            return httpx.Response(201, json={"name": params.get("branch")})
        if rest == "/repository/branches":
            return httpx.Response(200, json=[{"name": "12-login", "commit": {"id": "c1", "committed_date": _jetzt()}}])
        return httpx.Response(404, json={})


def _client(server: GitLab) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=server.transport())


# ── Repo-Angabe ──────────────────────────────────────────────────────────────

class RepoEingabe(unittest.TestCase):
    def test_gitlab_com_erkennt_sich_selbst_mit_untergruppen(self):
        self.assertEqual(projekt_git.repo_pruefen("https://gitlab.com/gruppe/sub/repo.git"),
                         "https://gitlab.com/gruppe/sub/repo")
        self.assertEqual(projekt_git.repo_ziel("https://gitlab.com/gruppe/sub/repo"),
                         ("gitlab", "https://gitlab.com", "gruppe/sub/repo"))
        self.assertEqual(projekt_git.anbieter_fuer("https://gitlab.com/gruppe/sub/repo", None), "gitlab")

    def test_eigener_server_mit_anbieter_und_kopierter_adresse(self):
        roh = projekt_git.repo_pruefen("https://GitLab.Example.com:8443/team/sub/repo/-/issues/12", "gitlab")
        self.assertEqual(roh, "https://gitlab.example.com:8443/team/sub/repo")
        self.assertEqual(projekt_git.repo_ziel(roh, "gitlab"),
                         ("gitlab", "https://gitlab.example.com:8443", "team/sub/repo"))
        # Ohne Anbieter bleibt eine eigene Adresse Forgejo/Gitea — wie bisher.
        self.assertEqual(projekt_git.repo_ziel(roh).art, "forgejo")
        self.assertEqual(projekt_git.team_ziel(Team(repo=roh, repo_anbieter="gitlab")).art, "gitlab")

    def test_unsichere_oder_unklare_adressen_werden_abgelehnt(self):
        for falsch in ("http://gitlab.example.com/team/repo",            # kein https
                       "https://user:pw@gitlab.example.com/team/repo",   # Zugangsdaten in der Adresse
                       "https://oauth2:" + TOKEN + "@gitlab.example.com/team/repo",
                       "https://gitlab.example.com/team",                # nur die Gruppe
                       "https://gitlab.example.com/team/../admin",
                       "https://gitlab.example.com/team/repo?private_token=x",
                       "https://evil.example\\.gitlab.example.com/team/repo",
                       "https://[::1]/team/repo",
                       "team/repo",                                       # GitLab nur mit voller Adresse
                       "https://gitlab.example.com/" + "/".join(["g"] * 25)):
            with self.subTest(falsch=falsch), self.assertRaises(ValueError):
                projekt_git.repo_pruefen(falsch, "gitlab")
        with self.assertRaises(ValueError):
            projekt_git.repo_pruefen("https://gitlab.example.com/a/b", "bitbucket")

    def test_remote_mit_untergruppen_passt_nur_zum_eigenen_server(self):
        repo = "https://gitlab.example.com/team/sub/repo"
        self.assertTrue(projekt_git.repo_passt(repo, "git@gitlab.example.com:team/sub/repo.git", "gitlab"))
        self.assertTrue(projekt_git.repo_passt(repo, "https://oauth2:x@gitlab.example.com/team/sub/repo", "gitlab"))
        self.assertFalse(projekt_git.repo_passt(repo, "https://evil.example/team/sub/repo", "gitlab"))
        self.assertFalse(projekt_git.repo_passt(repo, "https://gitlab.example.com/team/repo", "gitlab"))


class Registry(unittest.TestCase):
    def test_gitlab_mit_und_ohne_server(self):
        selbst = get_git_host_provider("gitlab", BASIS + "/")
        self.assertIsInstance(selbst, GitLabHostProvider)
        self.assertEqual((selbst.api_base, selbst.egress_ziele()), (BASIS + "/api/v4", ["gitlab.example.com"]))
        self.assertEqual(get_git_host_provider("gitlab").api_base, "https://gitlab.com/api/v4")

    def test_agent_bekommt_platzhalter_und_server(self):
        env = GitLabHostProvider(BASIS).get_agent_env("aie-geheim-g1-0123456789ab")
        self.assertEqual(env, {"GITLAB_TOKEN": "aie-geheim-g1-0123456789ab", "GITLAB_HOST": BASIS})


# ── Anbieter gegen das nachgestellte GitLab ─────────────────────────────────

class Anbieter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gl = GitLab()
        self.a = GitLabHostProvider(BASIS)

    async def test_issues_als_board_form_mit_token_im_eigenen_kopf(self):
        for i in range(1, 106):
            self.gl.issue(i, labels=["bug"])
        self.gl.issues[3]["assignees"] = [{"id": 7, "username": "dev-bot"}]
        async with _client(self.gl) as c:
            issues = await self.a.list_open_issues(c, TOKEN, REPO, 300)
        self.assertEqual([i["number"] for i in issues], list(range(1, 106)))
        self.assertEqual(issues[2]["assignees"], [{"login": "dev-bot", "id": 7}])
        self.assertEqual((issues[0]["labels"], issues[0]["state"], issues[0]["body"]),
                         ([{"name": "bug"}], "open", "Text"))
        erste = self.gl.anfragen[0]
        self.assertEqual(erste.headers["private-token"], TOKEN)
        self.assertNotIn("authorization", erste.headers)
        self.assertTrue(erste.url.raw_path.decode().startswith(f"/api/v4/projects/{ROH}/issues"))
        self.assertEqual(len(self.gl.anfragen), 2)   # 100 + 5

    async def test_merge_requests_wie_pull_requests_und_ci_aus_pipeline(self):
        self.gl.mrs[4] = {"iid": 4, "title": "Fix", "description": "Closes #3", "state": "opened",
                          "source_branch": "3-login", "sha": "abc", "web_url": f"{BASIS}/{REPO}/-/merge_requests/4",
                          "labels": []}
        async with _client(self.gl) as c:
            mrs = await self.a.list_open_pulls(c, TOKEN, REPO, 30)
            self.assertEqual((mrs[0]["number"], mrs[0]["head"], mrs[0]["body"]),
                             (4, {"sha": "abc", "ref": "3-login"}, "Closes #3"))
            for zustand, erwartet in {"running": "ci_laeuft", "pending": "ci_laeuft", "success": "ci_gruen",
                                      "failed": "ci_rot", "canceled": "ci_rot", "manual": "offen"}.items():
                self.gl.pipelines["abc"] = zustand
                with self.subTest(zustand=zustand):
                    self.assertEqual(await self.a.checks_state(c, TOKEN, REPO, "abc"), erwartet)
            self.assertEqual(await self.a.checks_state(c, TOKEN, REPO, "ohne"), "offen")
            self.gl.mrs[4]["state"] = "merged"
            self.assertEqual(await self.a.pull_state(c, TOKEN, REPO, 4), "gemergt")
            self.gl.mrs[4]["state"] = "closed"
            self.assertEqual(await self.a.pull_state(c, TOKEN, REPO, 4), "geschlossen")

    async def test_labels_per_name_und_kommas_werden_abgewiesen(self):
        self.gl.issue(5, labels=["bug", "status/triage"])
        async with _client(self.gl) as c:
            self.assertTrue(await self.a.add_labels(c, TOKEN, REPO, 5, ["status/ready"]))
            self.assertTrue(await self.a.remove_label(c, TOKEN, REPO, 5, "status/triage"))
            self.assertFalse(await self.a.add_labels(c, TOKEN, REPO, 5, ["a,b"]))
            self.assertFalse(await self.a.remove_label(c, TOKEN, REPO, 5, "a,b"))
        self.assertEqual(self.gl.issues[5]["labels"], ["bug", "status/ready"])
        puts = [json.loads(r.content) for r in self.gl.anfragen if r.method == "PUT"]
        self.assertEqual(puts, [{"add_labels": "status/ready"}, {"remove_labels": "status/triage"}])

    async def test_zustaendige_austragen_behaelt_die_uebrigen(self):
        self.gl.issue(6, assignees=[{"id": 7, "username": "dev-bot"}, {"id": 8, "username": "Gast"}])
        async with _client(self.gl) as c:
            self.assertTrue(await self.a.remove_assignees(c, TOKEN, REPO, 6, ["DEV-BOT"]))
        self.assertEqual(self.gl.issues[6]["assignees"], [{"id": 8, "username": "Gast"}])

    async def test_kommentare_ohne_systemeintraege_und_vertraut_nach_rolle(self):
        self.gl.issue(7)
        self.gl.note(7, "added ~bug label", system=True)
        self.gl.note(7, "@ai-employee A2: Frage", autor_id=7, autor="dev-bot")     # Developer
        self.gl.note(7, "@ai-employee A2: Frage", autor_id=8, autor="gast")        # Guest
        self.gl.note(7, "@ai-employee A2: Frage", autor_id=55, autor="fremd")      # kein Mitglied
        async with _client(self.gl) as c:
            k = await self.a.list_issue_comments(c, TOKEN, REPO, 7)
        self.assertEqual([(x["autor"], x["vertraut"]) for x in k],
                         [("dev-bot", True), ("gast", False), ("fremd", False)])
        self.assertEqual(self.gl.anfragen[0].url.params["sort"], "asc")

    async def test_kommentar_aendern_braucht_das_issue(self):
        self.gl.issue(8)
        self.gl.note(8, "alt")
        nid = self.gl.notes[8][0]["id"]
        async with _client(self.gl) as c:
            self.assertFalse(await self.a.kommentar_aendern(c, TOKEN, REPO, nid, "neu"))
            self.assertTrue(await self.a.kommentar_aendern(c, TOKEN, REPO, nid, "neu", nummer=8))
        self.assertEqual(self.gl.notes[8][0]["body"], "neu")

    async def test_merge_request_statt_pull_request_und_vorhandener_wird_gefunden(self):
        async with _client(self.gl) as c:
            await self.a.branch_anlegen(c, TOKEN, REPO, "ai-employee/x", "main")
            url = await self.a.pull_anlegen(c, TOKEN, REPO, "ai-employee/x", "main", "Titel", "Text")
            nochmal = await self.a.pull_anlegen(c, TOKEN, REPO, "ai-employee/x", "main", "Titel", "Text")
        self.assertEqual(url, f"{BASIS}/{REPO}/-/merge_requests/1")
        self.assertEqual(nochmal, url)
        anlegen = [json.loads(r.content) for r in self.gl.anfragen
                   if r.method == "POST" and r.url.raw_path.decode().endswith("/merge_requests")][0]
        self.assertEqual((anlegen["source_branch"], anlegen["target_branch"]), ("ai-employee/x", "main"))

    async def test_sichtbarkeit_und_konto(self):
        async with _client(self.gl) as c:
            self.assertIs(await self.a.repo_oeffentlich(c, TOKEN, REPO), False)
            self.assertEqual(await self.a.eigenes_konto(c, TOKEN), "board-bot")
            self.assertIsNone(await self.a.eigenes_konto(c, ""))

    async def test_weiterleitung_wird_nicht_verfolgt_der_token_bleibt_beim_server(self):
        gesehen = []

        def handler(request):
            gesehen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://andere.example/abgreifen"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            with self.assertRaises(RuntimeError) as e:
                await self.a.list_open_issues(c, TOKEN, REPO, 10)
        self.assertEqual(len(gesehen), 1)
        self.assertNotIn(TOKEN, str(e.exception))

    async def test_ablehnung_nennt_das_recht_aber_nie_den_token(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))) as c:
            with self.assertRaises(RuntimeError) as e:
                await self.a.list_open_issues(c, TOKEN, REPO, 10)
            with self.assertRaises(RuntimeError) as e2:
                await self.a.comment_issue(c, TOKEN, REPO, 1, "x")
        for fehler in (e.exception, e2.exception):
            self.assertIn("gitlab.example.com", str(fehler))
            self.assertNotIn(TOKEN, str(fehler))

    async def test_fremde_antworten_ohne_form_stuerzen_nicht(self):
        antwort = [{"iid": "3", "title": "x"}, {"iid": True}, "text", {"iid": 4, "labels": [1, "ok"], "assignees": [5]}]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=antwort))) as c:
            issues = await self.a.list_open_issues(c, TOKEN, REPO, 10)
        self.assertEqual([i["number"] for i in issues], [None, None, 4])
        self.assertEqual((issues[2]["labels"], issues[2]["assignees"]), ([{"name": "ok"}], []))


# ── Derselbe Ablauf wie bei GitHub: Board, Rückfrage, Antwort, Merge Request ─

class GitLabProjekt(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gl = GitLab()
        self.anbieter = GitLabHostProvider(BASIS)
        echt = httpx.AsyncClient

        def client(*a, **kw):
            return echt(transport=self.gl.transport())

        self.patches = [patch("app.core.projekt_git.httpx.AsyncClient", client),
                        patch.object(u, "anlage_kennung", return_value=ANLAGE)]
        for p in self.patches:
            p.start()
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo, team.repo_anbieter, team.lead_agent_id = f"{BASIS}/{REPO}", "gitlab", "a1"
            await db.commit()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await super().asyncTearDown()

    async def _abgleich(self, db):
        async with _client(self.gl) as c:
            r = await projekt_git.abgleichen(db, await db.get(Team, "p1"), TOKEN, self.anbieter, c)
        await db.commit()
        return r

    async def _karte(self, db, nr):
        return (await db.execute(select(ProjektKarte).where(ProjektKarte.extern_nr == nr,
                                                            ProjektKarte.quelle == "issue"))).scalar_one()

    async def test_issue_wird_karte_und_merge_request_haengt_mit_ci_daran(self):
        self.gl.issue(12, "Login klemmt", labels=["status/ready"])
        self.gl.mrs[3] = {"iid": 3, "title": "Login reparieren", "description": "Closes #12", "state": "opened",
                          "source_branch": "12-login", "sha": "abc", "labels": [],
                          "web_url": f"{BASIS}/{REPO}/-/merge_requests/3"}
        self.gl.pipelines["abc"] = "success"
        async with self.Session() as db:
            r = await self._abgleich(db)
            karte = await self._karte(db, 12)
        self.assertEqual((r["neu"], r["offene_issues"], r["offene_prs"]), (1, 1, 1))
        self.assertEqual((karte.titel, karte.url, karte.pr_nr, karte.pr_zustand, karte.spalte),
                         ("Login klemmt", f"{BASIS}/{REPO}/-/issues/12", 3, "ci_gruen", "pruefung"))
        self.assertEqual(karte.erstellt_von, f"gitlab.example.com:{REPO}")
        pfade = {r.url.raw_path.decode() for r in self.gl.anfragen}
        self.assertTrue(all(p.startswith(f"/api/v4/projects/{ROH}") or p == "/api/v4/user" for p in pfade), pfade)

    async def test_geschlossenes_issue_ist_erledigt(self):
        self.gl.issue(13)
        async with self.Session() as db:
            await self._abgleich(db)
            self.gl.issues[13]["state"] = "closed"
            r = await self._abgleich(db)
            karte = await self._karte(db, 13)
        self.assertEqual((r["erledigt"], karte.spalte), (1, "erledigt"))

    async def test_rueckfrage_als_kommentar_und_antwort_weckt_den_agenten(self):
        """Abstimmung: Der Agent fragt (Triage-Label + Kommentar), ein Mensch antwortet im Issue."""
        self.gl.issue(14)
        async with self.Session() as db:
            await self._abgleich(db)                     # erster Abgleich: Karte entsteht
            team, karte = await db.get(Team, "p1"), await self._karte(db, 14)
            karte.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
            karte.spalte, karte.agent_id, karte.task_id = "in_arbeit", "a2", "t1"
            await db.commit()
            tags = await projekt_git.status_ins_repo(db, None, team, karte, "triage", agent_name="A2",
                                                     agent_id="a2", lauf="t1",
                                                     zugang_daten=(TOKEN, self.anbieter))
            self.assertIn("status/triage", tags)
            self.assertIn("status/triage", self.gl.issues[14]["labels"])
            self.assertIn("Rückfrage offen", self.gl.notes[14][-1]["body"])
            # Die Frage selbst schreibt der Agent als Kommentar ins Issue.
            with patch("app.core.dlp.evaluate_egress", new_callable=AsyncMock,
                       return_value=type("U", (), {"blocked": False, "output": "Soll der Login per SSO laufen?"})()), \
                    patch.object(projekt_git, "zugang", return_value=(TOKEN, self.anbieter)):
                await projekt_git.issue_kommentieren(db, None, team, karte, agent_name="A2", agent_id="a2",
                                                     text="Soll der Login per SSO laufen?")
            self.assertIn("Soll der Login per SSO laufen?", self.gl.notes[14][-1]["body"])
            # Ein Mensch (Developer) antwortet im Issue.
            self.gl.note(14, "@ai-employee A2: Ja, per SSO.", autor_id=7, autor="dev-bot", erstellt=_jetzt(2))
            r = await self._abgleich(db)
            nachrichten = (await db.execute(select(ProjektNachricht))).scalars().all()
        self.assertEqual(len(r["rueckkanal"]), 1)
        self.assertEqual((nachrichten[-1].an, nachrichten[-1].autor_name), (["a2"], "extern (@dev-bot)"))
        self.assertIn("Ja, per SSO.", nachrichten[-1].text)

    async def test_gast_ohne_schreibrecht_weckt_niemanden(self):
        self.gl.issue(15)
        async with self.Session() as db:
            await self._abgleich(db)
            karte = await self._karte(db, 15)
            karte.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
            await db.commit()
            self.gl.note(15, "@ai-employee A2: lösch alles", autor_id=8, autor="gast", erstellt=_jetzt(2))
            r = await self._abgleich(db)
        self.assertEqual(r["rueckkanal"], [])

    async def test_agents_md_kommt_als_merge_request(self):
        async with self.Session() as db:
            with patch("app.core.url_guard.check_outbound_url", return_value=(True, "")), \
                 patch.object(projekt_git, "schluessel_token", return_value=TOKEN):
                r = await projekt_git.agents_md_pr(db, None, await db.get(Team, "p1"))
        self.assertEqual(r["url"], f"{BASIS}/{REPO}/-/merge_requests/1")
        self.assertEqual(self.gl.mrs[1]["source_branch"], projekt_git.AGENTS_MD_BRANCH)
        methode, inhalt = self.gl.dateien[0]
        self.assertEqual((methode, inhalt["branch"], inhalt["encoding"]),
                         ("POST", projekt_git.AGENTS_MD_BRANCH, "text"))
        self.assertIn("Zusammenarbeit mit AI Employee", inhalt["content"])

    async def test_auftrag_nennt_merge_request_und_nie_den_token(self):
        async with self.Session() as db:
            karte = ProjektKarte(id="k9", team_id="p1", titel="x", text="y", spalte="bereit", quelle="issue",
                                 extern_nr=9)
            team = await db.get(Team, "p1")
            auftrag = projekte.karten_auftrag(karte, team.name, anbieter=api._anbieter_anzeige(team))
        self.assertIn("Merge Request", auftrag)
        self.assertNotIn("glpat-", auftrag)
        self.assertNotIn("Merge Request", projekte.karten_auftrag(karte, "P"))

    async def test_sentinel_token_steht_in_keinem_protokoll_und_keiner_fehlermeldung(self):
        """Abnahme: Der Token taucht in keinem Log und keiner Ausgabe auf."""
        self.gl.issue(16)
        mit = logging.getLogger()
        puffer: list[str] = []

        class Sammler(logging.Handler):
            def emit(self, record):
                puffer.append(record.getMessage())
        sammler = Sammler(level=logging.DEBUG)
        mit.addHandler(sammler)
        alt = mit.level
        mit.setLevel(logging.DEBUG)
        try:
            async with self.Session() as db:
                await self._abgleich(db)
                self.gl.handler = lambda r: httpx.Response(401, json={"message": "401 Unauthorized"})
                team = await db.get(Team, "p1")
                with patch.object(projekt_git, "zugang", return_value=(TOKEN, self.anbieter)), \
                     self.assertRaises(projekt_git.GitFehler) as e:
                    await projekt_git.projekt_abgleichen(db, None, team)
                fehler = (await db.get(Team, "p1")).repo_fehler
                daten = api._serialize(await db.get(Team, "p1"))
        finally:
            mit.removeHandler(sammler)
            mit.setLevel(alt)
        for text in [str(e.exception), fehler, json.dumps(daten), *puffer]:
            self.assertNotIn(TOKEN, text or "")
        self.assertEqual(daten["repo_anbieter"], "gitlab")


# ── Wer darf was: Repo setzen, Schlüssel, Adressen ──────────────────────────

class Zugang(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.models.agent_secret import AgentSecret, AgentSecretAssignment
        from app.models.oauth_integration import OAuthIntegration

        self.entschluesseln = patch("app.core.encryption.decrypt_token", lambda wert: wert[4:])
        self.entschluesseln.start()
        async with self.engine.begin() as conn:
            for model in (AgentSecret, AgentSecretAssignment, OAuthIntegration):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        async with self.Session() as db:
            db.add(AgentSecret(id=7, name="GitLab", key_name="GITLAB_TOKEN", value_encrypted="enc:" + TOKEN,
                               owner_id="ua", is_active=True))
            db.add(AgentSecret(id=8, name="geteilt", key_name="X", value_encrypted="enc:fremd", owner_id="ub",
                               is_active=True))
            await db.commit()

    async def asyncTearDown(self):
        self.entschluesseln.stop()
        await super().asyncTearDown()

    async def test_ersteller_setzt_gitlab_und_bekommt_den_gitlab_anbieter(self):
        async with self.Session() as db:
            d = await api.update_team("p1", api.UpdateTeam(repo=f"{BASIS}/{REPO}", repo_anbieter="gitlab",
                                                           repo_secret_id=7), user=MITGLIED, db=db)
            self.assertEqual((d["repo"], d["repo_anbieter"]), (f"{BASIS}/{REPO}", "gitlab"))
            with patch("app.core.url_guard.check_outbound_url", return_value=(True, "")):
                token, anbieter = await projekt_git.zugang(db, None, await db.get(Team, "p1"))
            self.assertEqual((token, anbieter.api_base), (TOKEN, BASIS + "/api/v4"))
            # Zurück auf GitHub: der Anbieter fällt weg.
            d = await api.update_team("p1", api.UpdateTeam(repo="o/r", repo_anbieter="github"), user=MITGLIED, db=db)
            self.assertEqual((d["repo"], d["repo_anbieter"]), ("o/r", "github"))
            self.assertIsNone((await db.get(Team, "p1")).repo_anbieter)

    async def test_nur_der_ersteller_aendert_repo_und_anbieter(self):
        from types import SimpleNamespace

        from app.models.user import UserRole

        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.geteilt_mit = ["ub"]
            await db.commit()
            with self.assertRaises(HTTPException) as e:
                await api.update_team("p1", api.UpdateTeam(repo=f"{BASIS}/{REPO}", repo_anbieter="gitlab"),
                                      user=SimpleNamespace(id="ub", role=UserRole.MEMBER), db=db)
        self.assertEqual(e.exception.status_code, 403)

    async def test_geteilter_schluessel_geht_nie_an_einen_gitlab_server(self):
        from unittest.mock import AsyncMock

        async with self.Session() as db:
            with patch("app.core.secret_zugriff.zugang", new=AsyncMock(return_value=object())):
                await api.update_team("p1", api.UpdateTeam(repo=f"{BASIS}/{REPO}", repo_anbieter="gitlab",
                                                           repo_secret_id=8), user=MITGLIED, db=db)
                with patch("app.core.url_guard.check_outbound_url", return_value=(True, "")), \
                        self.assertRaises(projekt_git.GitFehler) as e:
                    await projekt_git.zugang(db, None, await db.get(Team, "p1"))
        self.assertIn("eigenen Schlüssel", str(e.exception))

    async def test_interner_server_nur_mit_freigabe(self):
        async with self.Session() as db:
            await api.update_team("p1", api.UpdateTeam(repo="https://gitlab.intern.example/team/repo",
                                                       repo_anbieter="gitlab"), user=MITGLIED, db=db)
            team = await db.get(Team, "p1")
            intern = (False, "zeigt auf ein internes Ziel")
            with patch("app.core.url_guard.check_outbound_url", return_value=intern), \
                    patch("app.core.url_guard.firmennetz", return_value=True), \
                    self.assertRaises(projekt_git.GitFehler) as e:
                await projekt_git.zugang(db, None, team)
            self.assertIn("internes Ziel", str(e.exception))
            with patch("app.core.url_guard.check_outbound_url", return_value=intern), \
                    patch("app.core.url_guard.firmennetz", return_value=True), \
                    patch("app.config.settings.git_interne_server", "gitlab.intern.example", create=True):
                _, anbieter = await projekt_git.zugang(db, None, team)
            self.assertIsInstance(anbieter, GitLabHostProvider)


class Adresspruefung(unittest.TestCase):
    """SSRF: nur https, keine Zugangsdaten, intern nur nach Freigabe — und nie Loopback/Metadaten."""

    @staticmethod
    def _aufloesen(*ips):
        return lambda host, port: [(0, 0, 0, "", (ip, 0)) for ip in ips]

    def test_firmennetz_nur_private_netze(self):
        from app.core.url_guard import firmennetz

        self.assertTrue(firmennetz("g.example", self._aufloesen("10.1.2.3")))
        self.assertTrue(firmennetz("g.example", self._aufloesen("100.64.0.7")))
        for ip in ("127.0.0.1", "169.254.169.254", "0.0.0.0", "8.8.8.8", "::1", "fe80::1", "240.0.0.1"):
            with self.subTest(ip=ip):
                self.assertFalse(firmennetz("g.example", self._aufloesen(ip)))
        self.assertFalse(firmennetz("g.example", self._aufloesen("10.0.0.1", "127.0.0.1")))  # eine reicht
        self.assertFalse(firmennetz("metadata.google.internal", self._aufloesen("10.0.0.1")))

    def test_server_pruefen(self):
        intern = (False, "zeigt auf ein internes Ziel")
        with patch("app.config.settings.git_interne_server", "gitlab.intern.example", create=True), \
                patch("app.core.url_guard.check_outbound_url", return_value=intern):
            with patch("app.core.url_guard.firmennetz", return_value=True):
                self.assertTrue(projekt_git.server_pruefen("https://gitlab.intern.example")[0])
                self.assertFalse(projekt_git.server_pruefen("https://andere.intern.example")[0])
                self.assertFalse(projekt_git.server_pruefen("http://gitlab.intern.example")[0])
                self.assertFalse(projekt_git.server_pruefen("https://u:p@gitlab.intern.example")[0])
            with patch("app.core.url_guard.firmennetz", return_value=False):   # zeigt auf Loopback o. Ä.
                self.assertFalse(projekt_git.server_pruefen("https://gitlab.intern.example")[0])

    def test_freigabeliste_nur_servernamen(self):
        self.assertEqual(projekt_git.interne_server_pruefen("GitLab.Intern.Example.\n git2.example.com, "),
                         "gitlab.intern.example,git2.example.com")
        for falsch in ("https://gitlab.intern.example", "*.intern.example", "10.0.0.0/8", "localhost",
                       "gitlab.intern.example/pfad"):
            with self.subTest(falsch=falsch), self.assertRaises(ValueError):
                projekt_git.interne_server_pruefen(falsch)


# ── Zertifizierungsstelle der Firma ─────────────────────────────────────────

def _ca() -> tuple[str, str]:
    """(Zertifikat, privater Schlüssel) einer frischen Test-Stelle als PEM."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    schluessel = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test-CA example.com")])
    jetzt = datetime.now(timezone.utc)
    zert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(schluessel.public_key())
            .serial_number(1).not_valid_before(jetzt - timedelta(days=1)).not_valid_after(jetzt + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(schluessel, hashes.SHA256()))
    return (zert.public_bytes(serialization.Encoding.PEM).decode(),
            schluessel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode())


class EigeneZertifizierungsstelle(unittest.TestCase):
    def test_nur_oeffentliche_zertifikate(self):
        zert, schluessel = _ca()
        self.assertIn("BEGIN CERTIFICATE", projekt_git.ca_pruefen(zert))
        self.assertEqual(projekt_git.ca_pruefen("  "), "")
        for falsch in (schluessel, zert + "\n" + schluessel, "kein Zertifikat", zert * 11):
            with self.subTest(falsch=falsch[:30]), self.assertRaises(ValueError):
                projekt_git.ca_pruefen(falsch)

    def test_client_eigener_server_prueft_mit_der_stelle_github_unveraendert(self):
        zert, _ = _ca()
        kontext = projekt_git.ssl_kontext(zert)
        self.assertIsInstance(kontext, ssl.SSLContext)
        self.assertEqual(kontext.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(any("Test-CA" in str(c.get("subject")) for c in kontext.get_ca_certs()))
        self.assertIs(projekt_git.ssl_kontext(""), True)
        with patch("app.config.settings.git_ca_zertifikate", zert, create=True), \
                patch.object(projekt_git.httpx, "AsyncClient") as client:
            projekt_git.git_client(5.0, GitLabHostProvider(BASIS))
            self.assertIsInstance(client.call_args.kwargs["verify"], ssl.SSLContext)
            self.assertFalse(client.call_args.kwargs["follow_redirects"])
            projekt_git.git_client(5.0, object())
            self.assertIs(client.call_args.kwargs["verify"], True)

    def test_agent_und_proxy_bekommen_nur_das_zertifikat(self):
        zert, _ = _ca()
        with patch("app.config.settings.git_ca_zertifikate", zert, create=True), \
                patch("app.config.settings.git_interne_server", "gitlab.intern.example", create=True):
            self.assertEqual(egress.git_ca_umgebung(), {"AIE_GIT_CA": zert.strip()})
            self.assertEqual(egress.git_server_fuer_proxy(),
                             {"git_ca": zert.strip(), "interne_ziele": ["gitlab.intern.example"]})
        with patch("app.config.settings.git_ca_zertifikate", "", create=True), \
                patch("app.config.settings.git_interne_server", "", create=True):
            self.assertEqual((egress.git_ca_umgebung(), egress.git_server_fuer_proxy()), ({}, {}))

    def test_einstellungen_lassen_sich_speichern(self):
        from app.services.settings_service import ALLOWED_KEYS, SECRET_KEYS

        self.assertTrue({"git_ca_zertifikate", "git_interne_server"} <= ALLOWED_KEYS)
        self.assertFalse({"git_ca_zertifikate", "git_interne_server"} & SECRET_KEYS)


class EgressProxy(unittest.TestCase):
    """Proxy-Seite (egress/egress_logik.py): freigegebene interne Git-Server, Stelle der Firma."""

    @classmethod
    def setUpClass(cls):
        import importlib
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "egress"))
        cls.logik = importlib.import_module("egress_logik")

    def _aufloesen(self, ip):
        return lambda host, port, type=None: [(0, 0, 0, "", (ip, port))]

    def test_interner_server_nur_mit_freigabe_und_nur_443(self):
        L = self.logik
        with self.assertRaises(L.Abgelehnt):
            L.ziel_adresse("gitlab.intern.example", 443, self._aufloesen("10.0.0.5"))
        self.assertEqual(L.ziel_adresse("gitlab.intern.example", 443, self._aufloesen("10.0.0.5"),
                                        interne=frozenset({"gitlab.intern.example"})), "10.0.0.5")
        for host, port, ip in (("gitlab.intern.example", 22, "10.0.0.5"),        # nur https-Port
                               ("gitlab.intern.example", 443, "127.0.0.1"),       # nie Loopback
                               ("gitlab.intern.example", 443, "169.254.169.254"),  # nie Metadaten
                               ("andere.intern.example", 443, "10.0.0.6")):
            with self.subTest(host=host, port=port, ip=ip), self.assertRaises(L.Abgelehnt):
                L.ziel_adresse(host, port, self._aufloesen(ip), interne=frozenset({"gitlab.intern.example"}))

    def test_stelle_und_freigabe_aus_der_anmeldung(self):
        L = self.logik
        zert, schluessel = _ca()
        antwort = {"hosts": ["gitlab.example.com"], "git_ca": zert, "interne_ziele": ["GitLab.Intern.Example"]}
        orch = L.Orchestrator("http://o", "t", senden=lambda pfad, daten: (200, antwort))
        self.assertEqual(orch.hosts(("a1", "tok")), ["gitlab.example.com"])
        self.assertEqual((orch.git_ca, orch.interne), (zert, frozenset({"gitlab.intern.example"})))
        buendel = L.ca_buendel("STANDARD\n", zert + "\n" + schluessel)
        self.assertTrue(buendel.startswith("STANDARD"))
        self.assertIn("BEGIN CERTIFICATE", buendel)
        self.assertNotIn("PRIVATE KEY", buendel)
        self.assertEqual(L.ca_buendel("STANDARD\n", "kaputt"), "STANDARD\n")


if __name__ == "__main__":
    unittest.main()
