"""Zusammenarbeit am Repo, Stufe 2 (#1120): Ablauf, Lebenszeichen, Obergrenze, Übergabe, Konflikt.

Gleiche Grundlage wie ``test_zusammenarbeit_repo`` (nachgestelltes Repo, echte
SQLite-Datenbank), dazu Commit-Autoren und das Ändern von Kommentaren.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_git, projekte
from app.core import uebernahme as u
from app.models.projekt_karte import ProjektKarte
from app.models.task import Task, TaskStatus
from app.models.team import Team
from tests.test_projekt_git import _issue
from tests.test_projekte import MITGLIED, _Router, agent
from tests.test_zusammenarbeit_repo import ANLAGE, _Basis, _kopf, _RepoMitKommentaren


def _vor(stunden: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=stunden)).isoformat(timespec="seconds")


class _Stufe2Repo(_RepoMitKommentaren):
    def __init__(self):
        super().__init__()
        self.commits: dict[str, dict] = {}
        self.geaendert: list[tuple] = []

    async def commit_info(self, client, token, repo, sha):
        return self.commits.get(sha)

    async def kommentar_aendern(self, client, token, repo, kommentar_id, body):
        self.geaendert.append((kommentar_id, body))
        return True


class _Stufe2(_Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gh = _Stufe2Repo()

    async def _issue_karte(self, db, nr, **kw):
        k = ProjektKarte(id=f"k{nr}", team_id="p1", titel=f"#{nr}", text="t", quelle="issue", extern_nr=nr,
                         spalte=kw.pop("spalte", "bereit"), **kw)
        db.add(k)
        await db.commit()
        return k


class AblaufUndLebenszeichen(_Stufe2):
    async def test_ablauf_aus_der_kopfzeile(self):
        self.gh.issues = [_issue(40, labels=["status/in-arbeit"]), _issue(41, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[40] = [_kopf("claude-code", seit=_vor(3), bis=_vor(1))]
        self.gh.kommentare_je_issue[41] = [_kopf("claude-code", seit=_vor(3), bis=_vor(-1))]
        async with self.Session() as db:
            await self._lauf(db)
            self.assertTrue(projekt_git.extern_stockt(await self._karte(db, 40)))
            frisch = await self._karte(db, 41)
            self.assertFalse(projekt_git.extern_stockt(frisch))
            self.assertIsNotNone(projekte.karte_dict(frisch)["extern_bis"])

    async def test_commit_ist_ein_lebenszeichen_und_verlaengert(self):
        self.gh.issues = [_issue(42, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[42] = [_kopf("claude-code", seit=_vor(5), bis=_vor(3))]
        self.gh.branches = [{"name": "42-x", "sha": "c42"}]
        self.gh.commits = {"c42": {"zeit": _vor(0.5), "autor_name": "Dev", "autor_mail": "dev@example.com"}}
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 42)
            self.assertFalse(projekt_git.extern_stockt(k))
            self.assertIsNone(k.konflikt)

    async def test_geaenderte_kopfzeile_ist_ein_lebenszeichen(self):
        kommentar = _kopf("claude-code", seit=_vor(5), bis=_vor(3))
        kommentar["geaendert"] = _vor(0.2)
        g = u.lesen(kommentar, ANLAGE)
        self.assertFalse(g.abgelaufen(dauer=u.DAUER))
        self.assertTrue(u.lesen(_kopf("claude-code", seit=_vor(5), bis=_vor(3)), ANLAGE).abgelaufen())

    async def test_dauer_je_projekt(self):
        self.gh.issues = [_issue(43, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[43] = [_kopf("claude-code", seit=_vor(3))]  # ohne bis: seit + Dauer
        async with self.Session() as db:
            await self._lauf(db)
            self.assertTrue(projekt_git.extern_stockt(await self._karte(db, 43)))
            (await db.get(Team, "p1")).uebernahme_stunden = 4
            await db.commit()
            await self._lauf(db)
            self.assertFalse(projekt_git.extern_stockt(await self._karte(db, 43)))

    async def test_eigene_uebernahme_wird_verlaengert_solange_der_lauf_laeuft(self):
        self.gh.issues = [_issue(44, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[44] = [_kopf(f"ai-employee:{ANLAGE}/A1", kid=77, agent_id="a1", lauf="t-44",
                                                  seit=_vor(1.5), bis=_vor(-0.5))]
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 44)
            k.spalte, k.task_id, k.agent_id = "in_arbeit", "t-44", "a1"
            k.kommentare_gelesen_at = None
            db.add(Task(id="t-44", title="Issue #44", prompt="p", agent_id="a1", status=TaskStatus.RUNNING))
            await db.commit()
            await self._lauf(db)
            self.assertEqual(len(self.gh.geaendert), 1)
            kid, text = self.gh.geaendert[0]
            neu = u.lesen({"id": kid, "body": text, "erstellt": _vor(0)}, ANLAGE)
            self.assertEqual((kid, neu.lauf, neu.agent_id), (77, "t-44", "a1"))
            self.assertGreater(neu.bis, datetime.now(timezone.utc) + timedelta(hours=1, minutes=50))
            # Lauf vorbei: nicht mehr verlängern.
            (await db.get(Task, "t-44")).status = TaskStatus.COMPLETED
            k = await self._karte(db, 44)
            k.uebernahme = {**k.uebernahme, "bis": _vor(-0.2)}
            await db.commit()
            await self._lauf(db)
            self.assertEqual(len(self.gh.geaendert), 1)


class Konflikte(_Stufe2):
    async def test_zwei_gueltige_uebernahmen_rot_und_einmal_gemeldet(self):
        from sqlalchemy import select

        from app.models.notification import Notification
        from app.models.projekt_nachricht import ProjektNachricht

        self.gh.issues = [_issue(50, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[50] = [
            _kopf("claude-code:Laptop", kid=1, zeit=_vor(1), seit=_vor(1), bis=_vor(-1)),
            _kopf(f"ai-employee:{ANLAGE}/A2", kid=2, zeit=_vor(0.5), agent_id="a2", seit=_vor(0.5), bis=_vor(-1.5)),
        ]
        async with self.Session() as db:
            (await db.get(Team, "p1")).lead_agent_id = "a1"
            await db.commit()
            await self._lauf(db)
            k = await self._karte(db, 50)
            self.assertIn("Claude Code (Laptop)", k.konflikt)
            self.assertIn("A2", k.konflikt)
            self.assertEqual(projekte.karte_dict(k)["konflikt"], k.konflikt)
            await self._lauf(db)
            texte = [n.text for n in (await db.execute(select(ProjektNachricht))).scalars()]
            hinweise = (await db.execute(select(Notification))).scalars().all()
        self.assertEqual(len(texte), 1)
        self.assertIn("Konflikt an #50", texte[0])
        self.assertEqual(len(hinweise), 1)

    async def test_abgelaufene_aeltere_uebernahme_ist_kein_konflikt(self):
        self.gh.issues = [_issue(51, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[51] = [
            _kopf("claude-code", kid=1, zeit=_vor(5), seit=_vor(5), bis=_vor(3)),
            _kopf("mensch:anna", kid=2, zeit=_vor(0.5), seit=_vor(0.5), bis=_vor(-1.5)),
        ]
        async with self.Session() as db:
            await self._lauf(db)
            self.assertIsNone((await self._karte(db, 51)).konflikt)

    async def test_plattform_commit_auf_fremdem_branch(self):
        self.gh.issues = [_issue(52, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[52] = [_kopf("claude-code:Laptop", seit=_vor(1), bis=_vor(-1))]
        self.gh.branches = [{"name": "feat/52-login", "sha": "c52"}]
        self.gh.commits = {"c52": {"zeit": _vor(0.1), "autor_name": "A1", "autor_mail": "a1@ai-employee.local"}}
        async with self.Session() as db:
            await self._lauf(db)
            k = await self._karte(db, 52)
            self.assertTrue(k.konflikt.startswith(projekt_git.KONFLIKT_COMMIT))
            self.assertIn("c52", k.konflikt)
            # Derselbe Commit wird nicht noch einmal abgefragt; der Befund bleibt.
            self.gh.commits = {}
            await self._lauf(db)
            self.assertIsNotNone((await self._karte(db, 52)).konflikt)

    def test_commit_autor_gegen_inhaber(self):
        plattform = u.Uebernahme(wer="x", art=u.PLATTFORM, name="A1", agent_id="a1")
        claude = u.Uebernahme(wer="claude-code", art=u.CLAUDE_CODE, name="Claude Code")
        alt = u.Uebernahme(wer="ai-employee:?/A1", art=u.PLATTFORM, name="A1", agent="A1")
        self.assertTrue(u.commit_passt(plattform, "a1@ai-employee.local"))
        self.assertFalse(u.commit_passt(plattform, "a2@ai-employee.local"))
        self.assertFalse(u.commit_passt(plattform, "dev@example.com"))
        self.assertTrue(u.commit_passt(claude, "dev@example.com"))
        self.assertFalse(u.commit_passt(claude, "a1@ai-employee.local"))
        self.assertTrue(u.commit_passt(alt, "a2@ai-employee.local"))
        self.assertTrue(u.commit_passt(claude, None))


class ObergrenzeUndAbloesung(_Stufe2):
    async def test_server_vergibt_nicht_ueber_die_obergrenze(self):
        async with self.Session() as db:
            await self._issue_karte(db, 60, spalte="in_arbeit", agent_id="a1", task_id="x1")
            await self._issue_karte(db, 61, spalte="in_arbeit", agent_id="a1", task_id="x2")
            await self._issue_karte(db, 62)
            t = await db.get(Team, "p1")
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)), \
                    self.assertRaises(HTTPException) as e:
                await api._vergeben(t, "k62", "a1", MITGLIED, None, _Router(db), db)
            self.assertEqual(e.exception.status_code, 409)
            self.assertIn("Obergrenze", e.exception.detail)
            t.max_uebernahmen = 3
            await db.commit()
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)), \
                    patch.object(projekt_git, "uebernahme_im_hintergrund"):
                r = await api._vergeben(t, "k62", "a1", MITGLIED, None, _Router(db), db)
            self.assertEqual(r["spalte"], "in_arbeit")

    async def test_board_warnt_bei_externen_ueber_der_grenze(self):
        self.gh.issues = [_issue(n, labels=["status/in-arbeit"]) for n in (70, 71, 72)]
        for n in (70, 71, 72):
            self.gh.kommentare_je_issue[n] = [_kopf("claude-code:Laptop", seit=_vor(0.2), bis=_vor(-2))]
        async with self.Session() as db:
            await self._lauf(db)
            b = await projekte.board(db, "p1", None)
        self.assertEqual(b["kennzahlen"]["obergrenze_uebernahmen"], 2)
        self.assertEqual({k["bearbeiter_uebernahmen"] for k in b["spalten"]["in_arbeit"]}, {3})

    async def _abgelaufen(self, db):
        self.gh.issues = [_issue(80, labels=["status/in-arbeit"])]
        self.gh.kommentare_je_issue[80] = [_kopf("claude-code:Laptop", seit=_vor(6), bis=_vor(4))]
        await self._lauf(db)
        k = await self._karte(db, 80)
        k.spalte = "bereit"  # vom Menschen eingeplant; die Übernahme steht noch im Repo
        await db.commit()
        return await db.get(Team, "p1"), k

    async def test_abgelaufene_fremde_uebernahme_nur_mit_erlaubnis(self):
        async with self.Session() as db:
            t, k = await self._abgelaufen(db)
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)), \
                    self.assertRaises(HTTPException) as e:
                await api._vergeben(t, k.id, "a1", MITGLIED, None, _Router(db), db)
            self.assertEqual(e.exception.status_code, 409)
            t.abgelaufene_uebernehmen = True
            await db.commit()
            gemeldet = []
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)), \
                    patch.object(projekt_git, "uebernahme_im_hintergrund",
                                 side_effect=lambda *a, **kw: gemeldet.append(kw)):
                r = await api._vergeben(t, k.id, "a1", MITGLIED, None, _Router(db), db)
        self.assertEqual(r["spalte"], "in_arbeit")
        self.assertIn("Claude Code (Laptop)", r["notiz"])
        self.assertEqual(gemeldet[0]["vorgaenger"].wer, "claude-code:Laptop")
        text = u.uebernahme_kommentar(agent_name="A1", agent_id="a1", lauf="t", nr=80, projekt_name="P",
                                      vorgaenger=gemeldet[0]["vorgaenger"])
        self.assertIn('vorher="claude-code:Laptop"', text)
        self.assertIn("abgelaufen", text)
        # Die neue Übernahme beendet die alte — kein Konflikt.
        alt = self.gh.kommentare_je_issue[80][0]
        befund = u.auswerten([alt, {"id": 9, "body": text, "erstellt": _vor(0)}], ANLAGE)
        self.assertEqual((befund.inhaber.agent_id, befund.konkurrent), ("a1", None))

    async def test_karenzzeit_nach_dem_ablauf(self):
        async with self.Session() as db:
            t, k = await self._abgelaufen(db)
            t.abgelaufene_uebernehmen = True
            k.extern_bearbeiter = None  # ohne Regung von der Karte
            jetzt = datetime.now(timezone.utc)
            kurz = u.Uebernahme(wer="claude-code", art=u.CLAUDE_CODE, name="Claude Code",
                                seit=jetzt - timedelta(hours=2, minutes=10), bis=jetzt - timedelta(minutes=10))
            self.assertFalse(projekt_git.abloesbar(t, k, kurz))
            kurz.bis -= u.KARENZ
            self.assertTrue(projekt_git.abloesbar(t, k, kurz))
            t.abgelaufene_uebernehmen = False
            self.assertFalse(projekt_git.abloesbar(t, k, kurz))


class Uebergabe(_Stufe2):
    async def _karte_in_arbeit(self, db):
        await self._issue_karte(db, 90, spalte="in_arbeit", agent_id="a1", task_id="t-alt")

    async def test_an_claude_code(self):
        async with self.Session() as db:
            await self._karte_in_arbeit(db)
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                r = await api.karte_uebergeben("p1", "k90", api.Uebergeben(
                    an="claude-code:Laptop", stand="Login gebaut, Tests fehlen", naechster_schritt="Tests schreiben"),
                    request=None, router_=_Router(db), user=agent("a1"), db=db, redis=None)
        self.assertEqual((r["spalte"], r["task_id"], r["agent_id"]), ("in_arbeit", None, None))
        self.assertEqual((r["extern_bearbeiter"], r["bearbeiter_art"]), ("Claude Code (Laptop)", u.CLAUDE_CODE))
        body = self.gh.kommentare[-1][1]
        gelesen = u.lesen({"id": 1, "body": body, "erstellt": _vor(0)}, ANLAGE)
        self.assertEqual((gelesen.wer, gelesen.vorher), ("claude-code:Laptop", f"ai-employee:{ANLAGE}/A1"))
        self.assertIn("**Stand:** Login gebaut, Tests fehlen", body)
        self.assertIn("**Nächster Schritt:** Tests schreiben", body)
        # Die alte Übernahme von A1 ist damit beendet: kein Konflikt.
        alt = _kopf(f"ai-employee:{ANLAGE}/A1", kid=0, zeit=_vor(1), agent_id="a1", seit=_vor(1), bis=_vor(-1))
        befund = u.auswerten([alt, {"id": 1, "body": body, "erstellt": _vor(0)}], ANLAGE)
        self.assertIsNone(befund.konkurrent)

    async def test_an_einen_agenten_des_projekts_mit_neuem_lauf(self):
        async with self.Session() as db:
            await self._karte_in_arbeit(db)
            router = _Router(db)
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)), \
                    patch.object(projekt_git, "uebernahme_im_hintergrund") as melden:
                r = await api.karte_uebergeben("p1", "k90", api.Uebergeben(
                    agent_id="a2", stand="Halb fertig", naechster_schritt="PR stellen"),
                    request=None, router_=router, user=MITGLIED, db=db, redis=None)
            melden.assert_not_called()  # die Übergabe hat die Übernahme schon geschrieben
        self.assertEqual((r["spalte"], r["agent_id"]), ("in_arbeit", "a2"))
        self.assertIn("## Übergabe", router.aufrufe[0]["prompt"])
        self.assertIn('[EXTERNAL-DATA source="uebergabe"]', router.aufrufe[0]["prompt"])
        gelesen = u.lesen({"id": 1, "body": self.gh.kommentare[-1][1], "erstellt": _vor(0)}, ANLAGE)
        self.assertEqual((gelesen.art, gelesen.agent_id), (u.PLATTFORM, "a2"))

    async def test_regeln(self):
        async with self.Session() as db:
            await self._karte_in_arbeit(db)
            faelle = [
                (api.Uebergeben(stand="s", naechster_schritt="n"), MITGLIED, 422),
                (api.Uebergeben(an="irgendwer", stand="s", naechster_schritt="n"), MITGLIED, 422),
                (api.Uebergeben(an="mensch:anna", stand="s", naechster_schritt="n"), agent("a2"), 403),
                (api.Uebergeben(agent_id="b1", stand="s", naechster_schritt="n"), MITGLIED, 400),
            ]
            for body, wer, status in faelle:
                with self.subTest(body=body), patch.object(projekt_git, "zugang", return_value=("t", self.gh)), \
                        self.assertRaises(HTTPException) as e:
                    await api.karte_uebergeben("p1", "k90", body, request=None, router_=_Router(db), user=wer,
                                               db=db, redis=None)
                self.assertEqual(e.exception.status_code, status)

    def test_empfaenger_von_aussen(self):
        with patch.object(u, "anlage_kennung", return_value=ANLAGE):
            self.assertEqual(u.wer_aus_eingabe("mensch:anna")[1], u.MENSCH)
            self.assertEqual(u.wer_aus_eingabe("ai-employee:anlage-nord/B")[1], u.ANDERE_ANLAGE)
            self.assertIsNone(u.wer_aus_eingabe("extern"))
            self.assertIsNone(u.wer_aus_eingabe("irgendwer"))
