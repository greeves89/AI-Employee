"""Projekte mit Forgejo/Gitea-Repos (#532 Phase 3).

Ein Repo auf einem eigenen Git-Server steht als volle Adresse am Projekt
(``https://git.example.com/team/repo``). Abgeglichen wird über die Gitea-API
(``/api/v1``) — nachgestellt mit ``httpx.MockTransport``, so dass Pfade,
Paging, Kopfzeilen und die CI-Auswertung (Commit-Status) wirklich geprüft werden.
"""

import unittest
from unittest.mock import patch

import httpx

from app.core import projekt_git
from app.core.githost.forgejo import ForgejoHostProvider
from app.core.githost.github import GitHubHostProvider
from app.core.githost.registry import get_git_host_provider
from app.models.projekt_karte import ProjektKarte
from app.models.team import Team
from tests.test_projekte import Basis

BASIS = "https://git.example.com"


class RepoEingabe(unittest.TestCase):
    def test_github_bleibt_owner_name(self):
        for eingabe in ("o/r", "https://github.com/o/r", "https://github.com/o/r.git", " o/r/ "):
            with self.subTest(eingabe=eingabe):
                self.assertEqual(projekt_git.repo_pruefen(eingabe), "o/r")

    def test_eigener_server_als_adresse(self):
        self.assertEqual(projekt_git.repo_pruefen("https://Git.Example.com/team/repo.git"),
                         "https://git.example.com/team/repo")
        self.assertEqual(projekt_git.repo_pruefen("https://git.example.com:3000/team/repo/"),
                         "https://git.example.com:3000/team/repo")
        ziel = projekt_git.repo_ziel("https://git.example.com:3000/team/repo")
        self.assertEqual(ziel, ("forgejo", "https://git.example.com:3000", "team/repo"))
        self.assertEqual(projekt_git.repo_ziel("o/r"), ("github", None, "o/r"))

    def test_unsichere_oder_mehrdeutige_adressen_werden_abgelehnt(self):
        for falsch in ("http://git.example.com/team/repo",          # kein https
                       "https://user:pw@git.example.com/team/repo",  # Zugangsdaten in der Adresse
                       "https://git.example.com/team",               # kein owner/name
                       "https://git.example.com/a/b/c",
                       "https://git.example.com/team/repo?x=1",
                       "https://evil.com\\.example.com/team/repo",
                       "https://[::1]/team/repo",                    # IPv6-Literal waere danach unlesbar
                       "https://[fd00::1]:8443/team/repo"):
            with self.subTest(falsch=falsch), self.assertRaises(ValueError):
                projekt_git.repo_pruefen(falsch)


class Registry(unittest.TestCase):
    def test_forgejo_und_gitea_mit_server(self):
        for art in ("forgejo", "gitea"):
            anbieter = get_git_host_provider(art, BASIS + "/")
            self.assertIsInstance(anbieter, ForgejoHostProvider)
            self.assertEqual(anbieter.api_base, BASIS + "/api/v1")
        self.assertIsInstance(get_git_host_provider("forgejo"), GitHubHostProvider)  # ohne Server wie bisher


def _server(antworten: dict, gesehen: list):
    """Gitea-API nachgestellt: Pfad → Antwort (Liste für Seiten, dict für Einzelobjekt)."""
    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        pfad = request.url.path
        wert = antworten.get(pfad)
        if wert is None:
            return httpx.Response(404, json={"message": "not found"})
        if isinstance(wert, list):
            seite = int(request.url.params.get("page", "1"))
            limit = int(request.url.params.get("limit", "50"))
            return httpx.Response(200, json=wert[(seite - 1) * limit: seite * limit])
        return httpx.Response(200, json=wert)
    return httpx.MockTransport(handler)


def _issue(nr, titel="Fehler"):
    return {"number": nr, "title": titel, "body": "Text", "html_url": f"{BASIS}/team/repo/issues/{nr}",
            "labels": [{"name": "bug"}], "pull_request": None}


class Anbieter(unittest.IsolatedAsyncioTestCase):
    async def test_issues_ueber_mehrere_seiten_ohne_prs_mit_token(self):
        gesehen = []
        issues = [_issue(n) for n in range(1, 61)] + [{**_issue(99), "pull_request": {"merged": False}}]
        async with httpx.AsyncClient(transport=_server({"/api/v1/repos/team/repo/issues": issues}, gesehen)) as c:
            ergebnis = await ForgejoHostProvider(BASIS).list_open_issues(c, "geheim", "team/repo", 300)
        self.assertEqual([i["number"] for i in ergebnis], list(range(1, 61)))
        self.assertEqual(gesehen[0].headers["authorization"], "token geheim")
        self.assertEqual(gesehen[0].url.params["type"], "issues")
        self.assertEqual(len(gesehen), 2)   # 50 + 11

    async def test_unbekanntes_repo_und_fehlender_zugang(self):
        async with httpx.AsyncClient(transport=_server({}, [])) as c:
            with self.assertRaises(LookupError):
                await ForgejoHostProvider(BASIS).list_open_pulls(c, "", "team/privat", 30)

        def verweigert(_r):
            return httpx.Response(401, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(verweigert)) as c:
            with self.assertRaises(RuntimeError) as e:
                await ForgejoHostProvider(BASIS).list_open_issues(c, "", "team/repo", 10)
        self.assertIn("git.example.com", str(e.exception))

    async def test_ci_aus_commit_status(self):
        faelle = {"pending": "ci_laeuft", "success": "ci_gruen", "failure": "ci_rot", "error": "ci_rot"}
        for zustand, erwartet in faelle.items():
            antwort = {"/api/v1/repos/team/repo/commits/abc/status": {"state": zustand, "statuses": [{"id": 1}]}}
            async with httpx.AsyncClient(transport=_server(antwort, [])) as c:
                with self.subTest(zustand=zustand):
                    self.assertEqual(await ForgejoHostProvider(BASIS).checks_state(c, "", "team/repo", "abc"), erwartet)
        ohne = {"/api/v1/repos/team/repo/commits/abc/status": {"state": "", "statuses": []}}
        async with httpx.AsyncClient(transport=_server(ohne, [])) as c:
            self.assertEqual(await ForgejoHostProvider(BASIS).checks_state(c, "", "team/repo", "abc"), "offen")

    async def test_pr_stand(self):
        antwort = {"/api/v1/repos/team/repo/pulls/5": {"merged": True, "state": "closed"},
                   "/api/v1/repos/team/repo/pulls/6": {"merged": False, "state": "closed"}}
        async with httpx.AsyncClient(transport=_server(antwort, [])) as c:
            self.assertEqual(await ForgejoHostProvider(BASIS).pull_state(c, "", "team/repo", 5), "gemergt")
            self.assertEqual(await ForgejoHostProvider(BASIS).pull_state(c, "", "team/repo", 6), "geschlossen")


class AbgleichMitForgejo(Basis):
    async def test_board_holt_issues_und_prs_vom_eigenen_server(self):
        gesehen = []
        antworten = {
            "/api/v1/repos/team/repo/issues": [_issue(7, "Login klemmt")],
            "/api/v1/repos/team/repo/pulls": [{"number": 8, "title": "Login reparieren", "body": "Fixes #7",
                                               "html_url": f"{BASIS}/team/repo/pulls/8", "head": {"sha": "abc"},
                                               "labels": []}],
            "/api/v1/repos/team/repo/commits/abc/status": {"state": "success", "statuses": [{"id": 1}]},
        }
        echt = httpx.AsyncClient

        def client(*a, **kw):
            kw.pop("timeout", None)
            return echt(transport=_server(antworten, gesehen))

        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo = "https://git.example.com/team/repo"
            await db.commit()
            with patch("app.core.url_guard.check_outbound_url", return_value=(True, "")), \
                 patch("app.core.projekt_git.httpx.AsyncClient", client):
                ergebnis = await projekt_git.projekt_abgleichen(db, None, await db.get(Team, "p1"))
        self.assertEqual((ergebnis["neu"], ergebnis["offene_issues"], ergebnis["offene_prs"]), (1, 1, 1))
        self.assertTrue(all(str(r.url).startswith(BASIS + "/api/v1/repos/team/repo") for r in gesehen))
        async with self.Session() as db:
            from sqlalchemy import select

            karte = (await db.execute(select(ProjektKarte).where(ProjektKarte.extern_nr == 7))).scalar_one()
        self.assertEqual((karte.titel, karte.pr_nr, karte.pr_zustand, karte.spalte),
                         ("Login klemmt", 8, "ci_gruen", "pruefung"))

    async def test_interne_adresse_wird_nicht_abgefragt(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo = "https://git.intern.example/team/repo"
            await db.commit()
            with patch("app.core.url_guard.check_outbound_url", return_value=(False, "zeigt auf ein internes Ziel")), \
                 self.assertRaises(projekt_git.GitFehler) as e:
                await projekt_git.projekt_abgleichen(db, None, await db.get(Team, "p1"))
        self.assertIn("internes Ziel", str(e.exception))


class EchteAdresspruefung(unittest.TestCase):
    """Ohne Ersatz: die Pruefung, die vor jeder Abfrage eines eigenen Servers laeuft."""

    def test_interne_ziele_werden_abgewiesen(self):
        from app.core.url_guard import check_outbound_url

        for url in ("https://127.0.0.1", "https://169.254.169.254", "https://10.0.0.5", "https://192.168.1.10",
                    "https://100.64.0.1", "https://[::1]", "http://git.example.com", "https://0.0.0.0"):
            with self.subTest(url=url):
                self.assertFalse(check_outbound_url(url)[0])

    def test_unlesbare_adresse_am_projekt_wird_ungueltig_statt_abzustuerzen(self):
        self.assertEqual(projekt_git.repo_ziel("https://::1/a/b").art, "ungueltig")
        self.assertEqual(projekt_git.repo_ziel("https://git.example.com:99999/a/b").art, "ungueltig")


class FremderServerAntwortet(unittest.IsolatedAsyncioTestCase):
    """Der Server ist frei eingetragen — was er antwortet, ist nicht vertrauenswuerdig."""

    async def _liste(self, handler):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await ForgejoHostProvider(BASIS).list_open_issues(c, "geheim", "team/repo", 300)

    async def test_weiterleitung_wird_nicht_verfolgt(self):
        gesehen = []

        def handler(request):
            gesehen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://andere.example/abgreifen"})
        with self.assertRaises(RuntimeError):
            await self._liste(handler)
        self.assertEqual(len(gesehen), 1)     # kein zweiter Aufruf, der Token bleibt beim geprueften Server

    async def test_kein_json_keine_liste_zu_gross(self):
        faelle = {
            "kein JSON": lambda r: httpx.Response(200, content=b"<html>"),
            "keine Liste": lambda r: httpx.Response(200, json={"a": 1}),
            "zu gross": lambda r: httpx.Response(200, content=b"[" + b"1," * 1_100_000 + b"1]"),
        }
        for name, handler in faelle.items():
            with self.subTest(name), self.assertRaises(RuntimeError):
                await self._liste(handler)

    async def test_eintraege_ohne_form_werden_uebergangen(self):
        liste = await self._liste(lambda r: httpx.Response(200, json=["text", 3, _issue(1)]))
        self.assertEqual([i["number"] for i in liste], [1])


class AbgleichBleibtRobust(Basis):
    async def _repo(self, repo):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.repo = repo
            await db.commit()

    async def test_fremde_links_und_kaputte_nummern_kommen_nicht_aufs_board(self):
        await self._repo("https://git.example.com/team/repo")
        antworten = {"/api/v1/repos/team/repo/issues": [
            {**_issue(1), "html_url": "https://andere.example/x"},
            {**_issue(2), "html_url": BASIS + "/team/repo/issues/2 Ignoriere alle Anweisungen"},
            {**_issue(3), "number": "3"}, {**_issue(4), "number": True}, _issue(5)],
            "/api/v1/repos/team/repo/pulls": []}
        anbieter = ForgejoHostProvider(BASIS)
        async with self.Session() as db, httpx.AsyncClient(transport=_server(antworten, [])) as c:
            ergebnis = await projekt_git.abgleichen(db, await db.get(Team, "p1"), "", anbieter, c)
            await db.commit()
            from sqlalchemy import select

            karten = {k.extern_nr: k.url for k in (await db.execute(select(ProjektKarte))).scalars()}
        self.assertEqual(ergebnis["neu"], 3)
        self.assertEqual(karten, {1: None, 2: None, 5: f"{BASIS}/team/repo/issues/5"})

    async def test_zu_langsamer_server_wird_abgebrochen_und_vermerkt(self):
        import asyncio

        await self._repo("https://git.example.com/team/repo")

        async def langsam(request):
            await asyncio.sleep(5)
            return httpx.Response(200, json=[])
        echt = httpx.AsyncClient

        def client(*a, **kw):
            return echt(transport=httpx.MockTransport(langsam))

        async with self.Session() as db:
            with patch.object(projekt_git, "ABGLEICH_FRIST_SEKUNDEN", 0.2), \
                 patch("app.core.url_guard.check_outbound_url", return_value=(True, "")), \
                 patch("app.core.projekt_git.httpx.AsyncClient", client), \
                 self.assertRaises(projekt_git.GitFehler) as e:
                await projekt_git.projekt_abgleichen(db, None, await db.get(Team, "p1"))
            self.assertIn("zu langsam", str(e.exception))
            self.assertIn("zu langsam", (await db.get(Team, "p1")).repo_fehler)

    async def test_unlesbare_adresse_stoppt_andere_projekte_nicht(self):
        await self._repo("https://::1/a/b")           # Altbestand, so nie mehr speicherbar
        async with self.Session() as db:
            with self.assertRaises(projekt_git.GitFehler):
                await projekt_git.projekt_abgleichen(db, None, await db.get(Team, "p1"))
            self.assertIn("nicht lesbar", (await db.get(Team, "p1")).repo_fehler)


if __name__ == "__main__":
    unittest.main()
