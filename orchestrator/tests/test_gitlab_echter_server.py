"""GitLab-Anbieter (#1194) gegen einen ECHTEN GitLab-Server (Plan Sicherheit 2026-11, Punkt 5).

``test_projekt_gitlab`` stellt GitLab mit ``httpx.MockTransport`` nach. Hier antwortet
ein echtes GitLab CE (Workflow ``gitlab-echt.yml``), erreicht wie ein selbst betriebener
Server im Firmennetz: eigener Name auf einer privaten Adresse, verbunden über
``projekt_git.git_client`` (feste Adresse gegen DNS-Rebinding), nur mit Freigabe durch
den Administrator (``git_interne_server``).

Geprüft wird der ganze Schreibweg des Boards: Issue, Labels, Kommentare (mit Rolle des
Autors), Branch, Datei, Merge Request, Pipeline-Stand, Schließen — und die Abweisung bei
fehlenden Rechten, ohne dass ein Token in einer Meldung steht.

Ohne ``GITLAB_ECHT_URL`` (lokal, normale CI) wird übersprungen.
"""

import os
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.core import projekt_git
from app.core.githost.gitlab import GitLabHostProvider

URL = os.environ.get("GITLAB_ECHT_URL", "")
REPO = os.environ.get("GITLAB_ECHT_REPO", "")
TOKEN = os.environ.get("GITLAB_ECHT_TOKEN", "")
TOKEN_REPORTER = os.environ.get("GITLAB_ECHT_TOKEN_REPORTER", "")
TOKEN_LESEN = os.environ.get("GITLAB_ECHT_TOKEN_LESEN", "")


def _freigabe(wert: str | None = None):
    if wert is None:
        teile = urlsplit(URL)
        wert = f"{teile.hostname}:{teile.port}"
    return patch.object(settings, "git_interne_server", wert)


def _nummer(web_url: str) -> int:
    return int(web_url.rstrip("/").rsplit("/", 1)[-1])


@unittest.skipUnless(URL and REPO and TOKEN and TOKEN_REPORTER and TOKEN_LESEN,
                     "nur im Workflow gitlab-echt.yml mit laufendem GitLab")
class GitLabEchterServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gl = GitLabHostProvider(URL)
        self.kennung = uuid.uuid4().hex[:8]

    def _client(self):
        return projekt_git.git_client(30, self.gl)

    async def test_ohne_freigabe_kein_zugriff(self):
        """Privater Server ohne Freigabe: die feste Verbindung lehnt ab."""
        with _freigabe(""):
            async with self._client() as c:
                with self.assertRaises(httpx.ConnectError):
                    await self.gl.eigenes_konto(c, TOKEN)

    async def test_schreibweg_des_boards(self):
        gl = self.gl
        with _freigabe():
            async with self._client() as c:
                ich = await gl.eigenes_konto(c, TOKEN)
                self.assertRegex(ich or "", r"^#\d+$")
                self.assertEqual(await gl.standard_branch(c, TOKEN, REPO), "main")
                self.assertIs(await gl.repo_oeffentlich(c, TOKEN, REPO), False)

                # Issue mit Labels, finden, lesen
                titel = f"Probe {self.kennung}"
                web = await gl.create_issue(c, TOKEN, REPO, titel, "Text der Probe", ["Status: Offen", "a,b"])
                self.assertTrue(web and web.startswith(URL + "/") and "/-/issues/" in web, web)
                nr = _nummer(web)
                self.assertEqual(await gl.search_open_issue(c, TOKEN, REPO, titel), nr)
                issue = await gl.get_issue(c, TOKEN, REPO, nr)
                self.assertEqual(issue["state"], "open")
                self.assertEqual([lbl["name"] for lbl in issue["labels"]], ["Status: Offen"],
                                 "Label mit Komma darf nicht zu zwei Labels werden")
                self.assertIn(nr, [i["number"] for i in await gl.list_open_issues(c, TOKEN, REPO)])
                self.assertIn(nr, [i["number"] for i in
                                   await gl.list_open_issues_with_label(c, TOKEN, REPO, "Status: Offen")])

                # Labels ändern
                self.assertTrue(await gl.add_labels(c, TOKEN, REPO, nr, ["Status: In Arbeit"]))
                self.assertIn("Status: In Arbeit", await gl.list_labels(c, TOKEN, REPO))
                self.assertTrue(await gl.remove_label(c, TOKEN, REPO, nr, "Status: Offen"))
                namen = [lbl["name"] for lbl in (await gl.get_issue(c, TOKEN, REPO, nr))["labels"]]
                self.assertEqual(namen, ["Status: In Arbeit"])

                # Kommentare: eigener (Owner = vertraut), fremder Reporter (nicht vertraut)
                await gl.comment_issue(c, TOKEN, REPO, nr, "Kommentar der Plattform")
                await gl.comment_issue(c, TOKEN_REPORTER, REPO, nr, "Kommentar eines Lesers")
                kommentare = await gl.list_issue_comments(c, TOKEN, REPO, nr)
                self.assertEqual([k["body"] for k in kommentare],
                                 ["Kommentar der Plattform", "Kommentar eines Lesers"],
                                 "Systemeinträge (Label-Änderungen) müssen fehlen")
                eigen, fremd = kommentare
                self.assertEqual(eigen["konto"], ich)
                self.assertTrue(eigen["vertraut"])
                self.assertFalse(fremd["vertraut"], "Reporter darf nicht schreiben, zählt also nicht")
                self.assertTrue(await gl.kommentar_aendern(c, TOKEN, REPO, eigen["id"], "geändert", nummer=nr))
                self.assertFalse(await gl.kommentar_aendern(c, TOKEN, REPO, eigen["id"], "x"))
                self.assertEqual((await gl.list_issue_comments(c, TOKEN, REPO, nr))[0]["body"], "geändert")

                # Branch, Datei, Merge Request (wie agents_md_pr)
                zweig = f"ai-employee/probe-{self.kennung}"
                await gl.branch_anlegen(c, TOKEN, REPO, zweig, "main")
                await gl.branch_anlegen(c, TOKEN, REPO, zweig, "main")  # gibt es schon: kein Fehler
                self.assertEqual(await gl.datei_holen(c, TOKEN, REPO, "docs/AGENTS.md", zweig), (None, None))
                await gl.datei_schreiben(c, TOKEN, REPO, "docs/AGENTS.md", "# Erst\n", zweig, "Probe anlegen", None)
                inhalt, sha = await gl.datei_holen(c, TOKEN, REPO, "docs/AGENTS.md", zweig)
                self.assertEqual(inhalt, "# Erst\n")
                self.assertTrue(sha)
                await gl.datei_schreiben(c, TOKEN, REPO, "docs/AGENTS.md", "# Zweit ä\n", zweig, "Probe ändern", sha)
                self.assertEqual((await gl.datei_holen(c, TOKEN, REPO, "docs/AGENTS.md", zweig))[0], "# Zweit ä\n")

                mr = await gl.pull_anlegen(c, TOKEN, REPO, zweig, "main", f"Probe {self.kennung}", "Text")
                self.assertTrue(mr and "/-/merge_requests/" in mr, mr)
                self.assertEqual(await gl.pull_anlegen(c, TOKEN, REPO, zweig, "main", "noch einmal", ""), mr,
                                 "zweiter Versuch liefert den offenen Merge Request")
                mr_nr = _nummer(mr)
                offen = {p["number"]: p for p in await gl.list_open_pulls(c, TOKEN, REPO)}
                self.assertIn(mr_nr, offen)
                self.assertEqual(offen[mr_nr]["head"]["ref"], zweig)
                self.assertEqual(await gl.pull_state(c, TOKEN, REPO, mr_nr), "offen")

                zweige = {b["name"]: b for b in await gl.list_branches(c, TOKEN, REPO)}
                self.assertIn(zweig, zweige)
                info = await gl.commit_info(c, TOKEN, REPO, zweige[zweig]["sha"])
                self.assertTrue(info and info["zeit"])
                self.assertEqual(await gl.checks_state(c, TOKEN, REPO, zweige[zweig]["sha"]), "offen")

                # Schließen
                await gl.close_issue(c, TOKEN, REPO, nr)
                self.assertEqual(await gl.issue_state(c, TOKEN, REPO, nr), "closed")
                self.assertIsNone(await gl.search_open_issue(c, TOKEN, REPO, titel))

    async def test_fehlende_rechte_abgewiesen_ohne_token_in_meldung(self):
        gl = self.gl
        with _freigabe():
            async with self._client() as c:
                web = await gl.create_issue(c, TOKEN, REPO, f"Rechte {self.kennung}", "", [])
                nr = _nummer(web)
                # Token nur mit read_api: lesen ja, schreiben nein
                self.assertEqual((await gl.get_issue(c, TOKEN_LESEN, REPO, nr))["number"], nr)
                self.assertIsNone(await gl.create_issue(c, TOKEN_LESEN, REPO, "darf nicht", "", []))
                with self.assertRaises(RuntimeError) as f:
                    await gl.comment_issue(c, TOKEN_LESEN, REPO, nr, "darf nicht")
                self.assertIn("verweigert", str(f.exception))
                # Reporter: kein Branch, keine Datei
                with self.assertRaises(RuntimeError) as f2:
                    await gl.branch_anlegen(c, TOKEN_REPORTER, REPO, f"verboten-{self.kennung}", "main")
                for meldung in (str(f.exception), str(f2.exception)):
                    for t in (TOKEN, TOKEN_LESEN, TOKEN_REPORTER):
                        self.assertNotIn(t, meldung)
                # falscher Token: Lesen der Liste scheitert laut, nicht still leer
                with self.assertRaises(RuntimeError):
                    await gl.list_open_issues(c, "glpat-falsch-" + self.kennung, REPO)


if __name__ == "__main__":
    unittest.main()
