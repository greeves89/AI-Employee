"""Zusammenarbeit mehrerer Agenten am selben Repo (#1116, Stufe 1 = #1117).

Board zeigt den echten Bearbeiter aus der Übernahme-Kopfzeile, die Push-Sperre
fragt live im Repo, Takt-Läufe pushen nicht, der AGENTS.md-Abschnitt kommt als
Pull Request. Das Repo ist nachgestellt (``_Anbieter`` aus test_projekt_git plus
Kommentare und Schreiben), die Datenbank echt (SQLite).
"""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_git, projekte
from app.core import uebernahme as u
from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus
from app.models.team import Team
from tests.test_projekt_git import Abgleich, _Anbieter, _issue
from tests.test_projekte import MITGLIED, Basis, agent

ANLAGE = "agents.example.com"


class _RepoMitKommentaren(_Anbieter):
    def __init__(self):
        super().__init__()
        self.kommentare_je_issue: dict[int, list[dict]] = {}
        self.kommentar_abrufe = 0
        self.dateien: dict[tuple[str, str], str] = {}
        self.prs: list[tuple] = []
        self.schreibrecht = True

    async def list_issue_comments(self, client, token, repo, nummer, hoechstens=300):
        self.kommentar_abrufe += 1
        return list(self.kommentare_je_issue.get(nummer, []))

    async def standard_branch(self, client, token, repo):
        return "main"

    async def datei_holen(self, client, token, repo, pfad, ref):
        inhalt = self.dateien.get((ref, pfad))
        return (inhalt, f"sha-{ref}") if inhalt is not None else (None, None)

    async def branch_anlegen(self, client, token, repo, neu, von):
        if not self.schreibrecht:
            raise RuntimeError("Dem Schlüssel fehlt das Recht Contents: write")
        if (von, "AGENTS.md") in self.dateien:
            self.dateien.setdefault((neu, "AGENTS.md"), self.dateien[(von, "AGENTS.md")])

    async def datei_schreiben(self, client, token, repo, pfad, inhalt, branch, nachricht, sha):
        self.dateien[(branch, pfad)] = inhalt

    async def pull_anlegen(self, client, token, repo, head, base, titel, text):
        self.prs.append((head, base, titel))
        return "https://github.com/o/r/pull/500"


def _kopf(wer, kid=1, zeit="2026-10-29T10:00:00Z", **felder):
    teile = " ".join(f'{k}="{v}"' for k, v in {"wer": wer, **felder}.items())
    return {"id": kid, "body": f"<!-- ai-employee:claim {teile} -->\nÜbernommen.", "erstellt": zeit}


class _Basis(Basis):
    """Wie ``test_projekt_git.Abgleich`` (Projekt p1 mit Repo o/r), ohne dessen Tests zu erben."""

    _lauf = Abgleich._lauf
    _karten = Abgleich._karten

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gh = _RepoMitKommentaren()
        async with self.Session() as db:
            (await db.get(Team, "p1")).repo = "o/r"
            await db.commit()
        self.anlage = patch.object(u, "anlage_kennung", return_value=ANLAGE)
        self.anlage.start()

    async def asyncTearDown(self):
        self.anlage.stop()
        await super().asyncTearDown()

    async def _karte(self, db, nr):
        return (await self._karten(db))[("issue", nr)]


class EchterBearbeiter(_Basis):
    async def test_claude_code_steht_mit_art_und_seit_an_der_karte(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [_kopf("claude-code:Laptop", seit="2026-10-29T09:30:00+00:00")]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 12)
            d = projekte.karte_dict(k)
        self.assertEqual((k.spalte, k.extern_bearbeiter, k.bearbeiter_art),
                         ("in_arbeit", "Claude Code (Laptop)", u.CLAUDE_CODE))
        self.assertEqual(d["extern_seit"].isoformat(), "2026-10-29T09:30:00+00:00")
        self.assertEqual((d["bearbeiter_art"], d["extern_agent_id"]), (u.CLAUDE_CODE, None))

    async def test_agent_dieser_anlage_ist_nicht_extern(self):
        self.gh.issues = [_issue(13, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[13] = [_kopf(f"ai-employee:{ANLAGE}/A2", agent_id="a2", lauf="t-anders")]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 13)
            d = projekte.karte_dict(k)
            text = projekte.board_als_text({"name": "P", "id": "p1"}, await projekte.board(db, "p1", None), {})
        self.assertEqual((k.extern_bearbeiter, d["bearbeiter_art"], d["extern_agent_id"]), ("A2", u.PLATTFORM, "a2"))
        self.assertIn("A2 (Agent dieser Anlage)", text)
        self.assertNotIn("extern", text.split("#13", 1)[1].split("\n", 1)[0])

    async def test_andere_anlage_und_mensch(self):
        self.gh.issues = [_issue(14, labels=["status/in-arbeit"]), _issue(15, labels=["in progress"])]
        self.gh.kommentare_je_issue[14] = [_kopf("ai-employee:anlage-nord/Builder")]
        self.gh.kommentare_je_issue[15] = [_kopf("mensch:anna")]
        async with self.Session() as db:
            await self._lauf(db)
            karten = await self._karten(db)
        self.assertEqual((karten[("issue", 14)].extern_bearbeiter, karten[("issue", 14)].bearbeiter_art),
                         ("Anlage anlage-nord: Builder", u.ANDERE_ANLAGE))
        self.assertEqual((karten[("issue", 15)].extern_bearbeiter, karten[("issue", 15)].bearbeiter_art),
                         ("@anna", u.MENSCH))

    async def test_eigener_lauf_bleibt_ohne_fremden_namen(self):
        self.gh.issues = [_issue(16, labels=["status/in-arbeit"])]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 16)
            k.spalte, k.task_id, k.agent_id = "in_arbeit", "t-eigen", "a1"
            k.kommentare_gelesen_at = None
            await db.commit()
            self.gh.kommentare_je_issue[16] = [_kopf(f"ai-employee:{ANLAGE}/A1", agent_id="a1", lauf="t-eigen")]
            await self._lauf(db)
            k = await self._karte(db, 16)
        self.assertEqual((k.extern_bearbeiter, k.bearbeiter_art), (None, None))

    async def test_zweiter_uebernehmer_neben_eigenem_lauf_wird_sichtbar(self):
        self.gh.issues = [_issue(17, labels=["status/in-arbeit"])]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 17)
            k.spalte, k.task_id, k.agent_id = "in_arbeit", "t-eigen", "a1"
            await db.commit()
            self.gh.issues[0]["updated_at"] = "2099-01-01T00:00:00Z"
            self.gh.kommentare_je_issue[17] = [
                _kopf(f"ai-employee:{ANLAGE}/A1", kid=1, agent_id="a1", lauf="t-eigen"),
                _kopf("claude-code", kid=2, zeit="2026-10-29T11:00:00Z"),
            ]
            await self._lauf(db)
            k = await self._karte(db, 17)
        self.assertEqual((k.spalte, k.extern_bearbeiter), ("in_arbeit", "Claude Code"))

    async def test_kommentare_nur_bei_aenderung_neu_gelesen(self):
        issue = _issue(18, labels=["status/in-arbeit"])
        issue["updated_at"] = "2026-10-29T10:00:00Z"
        self.gh.issues = [issue, _issue(19)]
        self.gh.kommentare_je_issue[18] = [_kopf("claude-code")]
        async with self.Session() as db:
            await self._lauf(db)
            await self._lauf(db)
            self.assertEqual(self.gh.kommentar_abrufe, 1)  # #19 ist nicht übernommen, #18 unverändert
            self.assertEqual((await self._karte(db, 18)).extern_bearbeiter, "Claude Code")
            issue["updated_at"] = "2099-01-01T00:00:00Z"
            self.gh.kommentare_je_issue[18].append(_kopf("mensch:anna", kid=2, zeit="2026-10-29T12:00:00Z"))
            await self._lauf(db)
            self.assertEqual(self.gh.kommentar_abrufe, 2)
            self.assertEqual((await self._karte(db, 18)).extern_bearbeiter, "@anna")

    async def test_obergrenze_der_kommentar_abfragen(self):
        self.gh.issues = [_issue(n, labels=["status/in-arbeit"]) for n in range(100, 130)]
        async with self.Session() as db:
            await self._lauf(db)
        self.assertEqual(self.gh.kommentar_abrufe, projekt_git.MAX_KOMMENTAR_ABFRAGEN)

    async def test_ohne_kopfzeile_bleibt_es_bei_tag_und_zustaendigen(self):
        issue = _issue(20)
        issue["assignees"] = [{"login": "dev-bot"}]
        self.gh.issues = [issue, _issue(21, labels=["status/in-arbeit"])]
        async with self.Session() as db:
            await self._lauf(db)
            karten = await self._karten(db)
        self.assertEqual((karten[("issue", 20)].extern_bearbeiter, karten[("issue", 20)].bearbeiter_art),
                         ("@dev-bot", u.MENSCH))
        self.assertEqual((karten[("issue", 21)].extern_bearbeiter, karten[("issue", 21)].bearbeiter_art),
                         ("extern", u.EXTERN))

    async def test_vor_dem_vergeben_nennt_die_live_pruefung_den_namen(self):
        self.gh.issues = [_issue(22)]
        async with self.Session() as db:
            await self._lauf(db)
            team = await db.get(Team, "p1")
            k = await self._karte(db, 22)
            self.gh.issues[0]["labels"] = [{"name": "status/in-arbeit"}]
            self.gh.kommentare_je_issue[22] = [_kopf("claude-code:Laptop")]
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                self.assertEqual(await projekt_git.extern_dran(db, None, team, k), "Claude Code (Laptop)")


class UebernahmeMitKopfzeile(_Basis):
    async def test_uebernahme_und_rueckgabe_tragen_die_kopfzeile(self):
        self.gh.issues = [_issue(30)]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 30)
        with patch("app.db.session.async_session_factory", self.Session), \
                patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
            await projekt_git.uebernahme_melden("p1", k.id, None, agent_name="A1", agent_id="a1", lauf="t-1")
            await projekt_git.uebernahme_melden("p1", k.id, None, agent_name="A1", agent_id="a1", lauf="t-1",
                                                uebernehmen=False)
        uebernahme, rueckgabe = [{"id": i, "body": b, "erstellt": f"2026-10-29T1{i}:00:00Z"}
                                 for i, (_, b) in enumerate(self.gh.kommentare)]
        gelesen = u.lesen(uebernahme, ANLAGE)
        self.assertEqual((gelesen.art, gelesen.agent_id, gelesen.lauf, gelesen.branch), (u.PLATTFORM, "a1", "t-1", "30-…"))
        self.assertTrue(u.lesen(rueckgabe, ANLAGE).freigabe)
        self.assertIsNone(u.inhaber([uebernahme, rueckgabe], ANLAGE))

    async def test_freigeben_schreibt_eine_rueckgabe_und_raeumt_auf(self):
        self.gh.issues = [_issue(31, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[31] = [_kopf("claude-code")]
        async with self.Session() as db:
            await self._lauf(db)
            team = await db.get(Team, "p1")
            k = await self._karte(db, 31)
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                await projekt_git.extern_freigeben(db, None, team, k)
            self.assertEqual((k.spalte, k.extern_bearbeiter, k.bearbeiter_art, k.uebernahme),
                             ("bereit", None, None, None))
        freigabe = {"id": 9, "body": self.gh.kommentare[-1][1], "erstellt": "2026-10-29T12:00:00Z"}
        self.assertIsNone(u.inhaber(self.gh.kommentare_je_issue[31] + [freigabe], ANLAGE))


class PushSperre(_Basis):
    """``push_pruefen``: wer darf in welchen Branch pushen (Antwort an den Hook)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.zugang = patch.object(projekt_git, "zugang", return_value=("t", self.gh))
        self.zugang.start()

    async def asyncTearDown(self):
        self.zugang.stop()
        await super().asyncTearDown()

    async def _pruefen(self, db, agent_id="a1", lauf=None, branch="12-fix", remote="https://github.com/o/r.git"):
        return await projekt_git.push_pruefen(db, None, agent_id, lauf=lauf,
                                              ziele=[{"branch": branch, "remote": remote}])

    async def test_fremde_uebernahme_sperrt_mit_klarer_meldung(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [_kopf("claude-code:Laptop", seit="2026-10-29T09:30:00+00:00")]
        async with self.Session() as db:
            r = await self._pruefen(db, branch="feat/12-login")
        self.assertFalse(r["erlaubt"])
        for teil in ("Claude Code (Laptop)", "29.10. 09:30", "feat/12-login", "im Issue nachfragen",
                     "Pull Request in", "freizugeben"):
            self.assertIn(teil, r["grund"])

    async def test_auch_force_push_eines_anderen_agenten_derselben_anlage(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [_kopf(f"ai-employee:{ANLAGE}/A2", agent_id="a2", lauf="t-a2")]
        async with self.Session() as db:
            self.assertFalse((await self._pruefen(db, agent_id="a1", lauf="t-a1"))["erlaubt"])
            # Der Inhaber selbst darf — in jedem seiner Läufe.
            self.assertTrue((await self._pruefen(db, agent_id="a2", lauf="t-andere"))["erlaubt"])

    async def test_andere_anlage_mit_gleichem_agentennamen_sperrt(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [_kopf("ai-employee:anlage-nord/A1", agent_id="a1")]
        async with self.Session() as db:
            self.assertFalse((await self._pruefen(db, agent_id="a1"))["erlaubt"])

    async def test_eigener_lauf_der_karte_darf(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 12)
            k.spalte, k.task_id, k.agent_id = "in_arbeit", "t-karte", "a1"
            await db.commit()
            self.assertTrue((await self._pruefen(db, lauf="t-karte"))["erlaubt"])
            # Tag gesetzt, aber kein Übernahme-Kommentar: die eigene Karte darf trotzdem.
            self.assertTrue((await self._pruefen(db, lauf="t-ander"))["erlaubt"])
            # Ein anderer Agent nicht.
            self.assertFalse((await self._pruefen(db, agent_id="a2"))["erlaubt"])

    async def test_alte_freitext_uebernahme_erkennt_den_namen(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [{"id": 1, "erstellt": "2026-10-29T10:00:00Z",
                                            "body": "Übernommen vom AI-Employee-Agenten „A1“ (Projekt-Board)."}]
        async with self.Session() as db:
            self.assertTrue((await self._pruefen(db, agent_id="a1"))["erlaubt"])
            self.assertFalse((await self._pruefen(db, agent_id="a2"))["erlaubt"])

    async def test_frei_ohne_nummer_ohne_projekt_ohne_uebernahme(self):
        self.gh.issues = [_issue(12)]
        async with self.Session() as db:
            self.assertTrue((await self._pruefen(db, branch="main"))["erlaubt"])
            self.assertTrue((await self._pruefen(db))["erlaubt"])  # niemand hat #12 übernommen
            self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
            self.gh.kommentare_je_issue[12] = [_kopf("claude-code")]
            # b1 ist in keinem Projekt mit Repo; ein fremdes Repo gehört zu keinem Projekt.
            self.assertTrue((await self._pruefen(db, agent_id="b1"))["erlaubt"])
            self.assertTrue((await self._pruefen(db, remote="git@github.com:andere/repo.git"))["erlaubt"])
            # Remote als ssh-Adresse desselben Repos: wird erkannt.
            self.assertFalse((await self._pruefen(db, remote="git@github.com:o/r.git"))["erlaubt"])

    async def test_repo_nicht_erreichbar_sperrt_nicht(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [_kopf("claude-code")]

        async def kaputt(*a, **kw):
            raise RuntimeError("GitHub antwortet nicht")

        self.gh.get_issue = kaputt
        async with self.Session() as db:
            self.assertTrue((await self._pruefen(db))["erlaubt"])

    async def test_ohne_remote_nur_bei_genau_einem_projekt(self):
        self.gh.issues = [_issue(12, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[12] = [_kopf("claude-code")]
        async with self.Session() as db:
            self.assertFalse((await self._pruefen(db, remote=None))["erlaubt"])
            db.add(Team(id="p3", name="Zweites", description="", member_agent_ids=["a1"], lead_agent_id=None,
                        is_active=True, created_by="ua", geteilt_mit=[], repo="o/zwei"))
            await db.commit()
            # Zwei Repos, keine Remote-Adresse: unklar, welches — nicht sperren.
            self.assertTrue((await self._pruefen(db, remote=None))["erlaubt"])

    async def test_takt_lauf_pusht_gar_nicht(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.takt_schedule_id = "s-takt"
            db.add(Schedule(id="s-takt", name="[Projekt] Projekt", prompt="p", interval_seconds=1800, agent_id="a1",
                            next_run_at=datetime.now(timezone.utc)))
            db.add(Task(id="t-takt", title="[Scheduled] [Projekt] Projekt", prompt="p", agent_id="a1",
                        status=TaskStatus.RUNNING, metadata_={"schedule_id": "s-takt", "team_id": "p1"}))
            db.add(Task(id="t-karte", title="Issue #5", prompt="p", agent_id="a1", status=TaskStatus.QUEUED,
                        metadata_={"team_id": "p1", "karte_id": "x"}))
            await db.commit()
            r = await self._pruefen(db, lauf="t-takt", branch="main")
            self.assertFalse(r["erlaubt"])
            self.assertIn("Arbeitstakt", r["grund"])
            # Ohne Lauf-Kennung: läuft gerade nur der Takt, gilt der Push als seiner.
            self.assertFalse((await self._pruefen(db, lauf=None, branch="main"))["erlaubt"])
            # Läuft daneben ein Karten-Lauf, ist es unklar — nicht sperren.
            (await db.get(Task, "t-karte")).status = TaskStatus.RUNNING
            await db.commit()
            self.assertTrue((await self._pruefen(db, lauf=None, branch="main"))["erlaubt"])
            self.assertTrue((await self._pruefen(db, lauf="t-karte", branch="main"))["erlaubt"])
            # Ein fremder Lauf zählt nicht als eigener.
            self.assertTrue((await self._pruefen(db, agent_id="a2", lauf="t-takt", branch="main"))["erlaubt"])

    async def test_endpunkt_nur_fuer_agenten(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.push_pruefen(api.PushPruefen(ziele=[]), user=MITGLIED, db=db, redis=None)
            self.assertEqual(e.exception.status_code, 403)
            r = await api.push_pruefen(api.PushPruefen(lauf=None, ziele=[api.PushZiel(branch="main")]),
                                       user=agent("a1"), db=db, redis=None)
            self.assertTrue(r["erlaubt"])

    def test_remote_adressen(self):
        f = projekt_git.remote_ziel
        self.assertEqual(f("https://x-access-token:geheim@github.com/O/R.git"), ("github.com", "o/r"))
        self.assertEqual(f("git@github.com:o/r.git"), ("github.com", "o/r"))
        self.assertEqual(f("ssh://git@git.example.com:2222/team/repo.git"), ("git.example.com", "team/repo"))
        self.assertIsNone(f("origin"))
        self.assertIsNone(f("https://github.com/nur-ein-teil"))
        self.assertTrue(projekt_git.repo_passt("https://git.example.com/team/repo", "https://git.example.com/team/repo"))
        self.assertFalse(projekt_git.repo_passt("https://git.example.com/team/repo", "https://evil.example/team/repo"))


class AgentsMd(_Basis):
    async def test_als_text_und_als_pull_request(self):
        async with self.Session() as db:
            text = await api.agents_md_text("p1", user=MITGLIED, db=db)
            self.assertIn("## Zusammenarbeit mit AI Employee", text["text"])
            self.gh.dateien[("main", "AGENTS.md")] = "# Regeln\n\nTests vor dem PR.\n"
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                r = await api.agents_md_vorschlagen("p1", user=MITGLIED, db=db, redis=None)
        self.assertEqual(r, {"url": "https://github.com/o/r/pull/500", "aktuell": False})
        neu = self.gh.dateien[(projekt_git.AGENTS_MD_BRANCH, "AGENTS.md")]
        self.assertTrue(neu.startswith("# Regeln"))
        self.assertIn(u.AGENTS_MD_START, neu)
        self.assertEqual(self.gh.prs[0][:2], (projekt_git.AGENTS_MD_BRANCH, "main"))
        # Nie direkt auf den Standard-Branch.
        self.assertNotIn(u.AGENTS_MD_START, self.gh.dateien[("main", "AGENTS.md")])

    async def test_schon_aktuell_kein_neuer_pr(self):
        self.gh.dateien[("main", "AGENTS.md")] = u.agents_md_einfuegen(None, u.agents_md_abschnitt())
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                self.assertEqual(await projekt_git.agents_md_pr(db, None, team), {"url": None, "aktuell": True})
        self.assertEqual(self.gh.prs, [])

    async def test_ohne_schreibrecht_klare_meldung(self):
        self.gh.schreibrecht = False
        async with self.Session() as db:
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                with self.assertRaises(HTTPException) as e:
                    await api.agents_md_vorschlagen("p1", user=MITGLIED, db=db, redis=None)
            self.assertEqual(e.exception.status_code, 424)
            self.assertIn("Contents: write", e.exception.detail)
            with patch.object(projekt_git, "zugang", return_value=("", self.gh)):
                with self.assertRaises(HTTPException) as e:
                    await api.agents_md_vorschlagen("p1", user=MITGLIED, db=db, redis=None)
            self.assertIn("Ohne Schlüssel", e.exception.detail)

    async def test_nur_der_ersteller_stellt_den_pr(self):
        from types import SimpleNamespace

        from app.models.user import UserRole

        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.geteilt_mit = ["ub"]
            await db.commit()
            with self.assertRaises(HTTPException) as e:
                await api.agents_md_vorschlagen("p1", user=SimpleNamespace(id="ub", role=UserRole.MEMBER),
                                                db=db, redis=None)
            self.assertEqual(e.exception.status_code, 403)
