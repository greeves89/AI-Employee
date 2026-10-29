"""Board mit Inbox, Triage und Freigabe nur durch Menschen.

Ablauf (wie im Repo per Tag): neue Karte → Inbox; ein Mensch gibt frei
(``status/ready`` → Bereit); ein Agent übernimmt (In Arbeit); hat er eine
Rückfrage, setzt er die Karte auf Triage (Tag, In-Arbeit-Tag weg) und hört auf;
der Mensch klärt und gibt wieder frei. Offener PR → Review, geschlossen →
Erledigt, „not planned“ → Verworfen.
"""

from unittest.mock import patch

from fastapi import HTTPException

from app.api import teams as api
from app.core import projekt_git, projekt_takt, projekte
from app.models.projekt_karte import SPALTEN, ProjektKarte
from app.models.task import Task, TaskStatus
from app.models.team import Team
from tests import test_projekt_git as _git
from tests.test_projekt_git import _Anbieter, _issue, _pr
from tests.test_projekte import MITGLIED, Basis, _Router, agent


class _Repo(_Anbieter):
    """Wie ``_Anbieter``, dazu Tags des Repos und geschlossene Issues mit Grund."""

    def __init__(self, tags=()):
        super().__init__()
        self.tags = list(tags)
        self.geschlossen: dict[int, str | None] = {}

    async def list_labels(self, client, token, repo, hoechstens=300):
        return list(self.tags)

    async def get_issue(self, client, token, repo, nummer):
        if nummer in self.geschlossen:
            return {"number": nummer, "state": "closed", "state_reason": self.geschlossen[nummer], "labels": []}
        return await super().get_issue(client, token, repo, nummer)


class TagVorrang(Basis):
    def test_vorrang_review_triage_in_arbeit_bereit(self):
        f = projekt_git._status_aus_tags
        self.assertEqual(f(["status/ready", "status/triage"]), "triage")
        self.assertEqual(f(["status/in-arbeit", "agent:blocked"]), "triage")
        self.assertEqual(f(["status/review", "status/triage"]), "pruefung")
        self.assertEqual(f(["status/in-arbeit", "status/ready"]), "in_arbeit")
        self.assertEqual(f(["status/ready"]), "bereit")
        self.assertIsNone(f(["bug"]))

    def test_triage_tags_mit_praefixen(self):
        for tag in ("status/triage", "triage", "needs-triage", "agent:blocked", "agent/blocked", "needs-human",
                    "claude:needs-human", "needs-info", "question", "Rückfrage", "rueckfrage", "Agent: Blocked"):
            self.assertEqual(projekt_git.tag_stand(tag), "triage", tag)
        # Ein bloßes „blocked“ ohne Agenten-Bezug ist keine Rückfrage.
        for tag in ("blocked", "status/blocked", "type:question-mark"):
            self.assertIsNone(projekt_git.tag_stand(tag), tag)

    def test_passendes_tag_nimmt_das_vorhandene(self):
        f = projekt_git.passendes_tag
        self.assertEqual(f("triage", ["bug", "agent:blocked", "triage"]), "agent:blocked")
        self.assertEqual(f("triage", ["bug"]), "status/triage")
        self.assertEqual(f("bereit", ["Ready"]), "Ready")
        self.assertEqual(f("in_arbeit", []), "status/in-arbeit")


class TriageImAbgleich(_git.Abgleich):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gh = _Repo()

    async def test_triage_tag_und_freigabe_im_repo(self):
        self.gh.issues = [_issue(1, labels=["status/ready"])]
        async with self.Session() as db:
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 1)].spalte, "bereit")
            self.gh.issues[0]["labels"] = [{"name": "agent:blocked"}, {"name": "status/ready"}]
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 1)].spalte, "triage")
            # Mensch klärt: Triage-Tag weg, Bereit-Tag gesetzt → Bereit.
            self.gh.issues[0]["labels"] = [{"name": "status/ready"}]
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 1)].spalte, "bereit")

    async def test_triage_tag_weg_ohne_bereit_heisst_inbox(self):
        self.gh.issues = [_issue(2, labels=["status/triage"])]
        async with self.Session() as db:
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 2)].spalte, "triage")
            self.gh.issues[0]["labels"] = []
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 2)].spalte, "eingang")

    async def test_rueckfrage_noch_nicht_im_repo_bleibt_in_triage(self):
        """Die Karte steht schon in Triage, das Tag kam (noch) nicht an: kein Zurück in die Inbox."""
        self.gh.issues = [_issue(3)]
        async with self.Session() as db:
            await self._lauf(db)
            k = (await self._karten(db))[("issue", 3)]
            k.spalte, k.task_id, k.agent_id = "triage", "t-frage", "a1"
            await db.commit()
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 3)].spalte, "triage")

    async def test_offener_pr_geht_vor_triage(self):
        self.gh.issues = [_issue(4, labels=["status/triage"])]
        async with self.Session() as db:
            await self._lauf(db)
            self.gh.pulls = [_pr(40, body="Closes #4")]
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 4)].spalte, "pruefung")

    async def test_not_planned_ist_verworfen_wieder_geoeffnet_inbox(self):
        self.gh.issues = [_issue(5), _issue(6), _issue(7)]
        async with self.Session() as db:
            await self._lauf(db)
            self.gh.issues = [_issue(7)]
            self.gh.geschlossen = {5: "not_planned", 6: "completed"}
            r = await self._lauf(db)
            karten = await self._karten(db)
            self.assertEqual(r["erledigt"], 2)
            self.assertEqual(karten[("issue", 5)].spalte, "verworfen")
            self.assertEqual(karten[("issue", 6)].spalte, "erledigt")
            self.assertIsNotNone(karten[("issue", 5)].erledigt_at)
            b = await projekte.board(db, "p1", None)
            self.assertEqual([k["extern_nr"] for k in b["spalten"]["verworfen"]], [5])
            self.assertEqual(b["kennzahlen"]["verworfen"], 1)
            self.assertEqual(set(b["spalten"]), set(SPALTEN))
            # Wieder geöffnet: zurück in die Inbox.
            self.gh.geschlossen = {}
            self.gh.issues = [_issue(5), _issue(7)]
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 5)].spalte, "eingang")

    async def test_ohne_grund_forgejo_ist_erledigt(self):
        self.gh.issues = [_issue(8)]
        async with self.Session() as db:
            await self._lauf(db)
            self.gh.issues = []
            self.gh.geschlossen = {8: None}
            await self._lauf(db)
            self.assertEqual((await self._karten(db))[("issue", 8)].spalte, "erledigt")


class NurMenschenGebenFrei(_git.Abgleich):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gh = _Repo(tags=["bug", "agent:blocked", "status/ready"])
        projekt_git._REPO_TAGS.clear()

    async def _issue_karte(self, db, nr, spalte, labels=(), **kw):
        k = projekte.neue_karte("p1", f"Issue {nr}", "Text", quelle="issue", extern_nr=nr, labels=list(labels))
        k.spalte = spalte
        for feld, wert in kw.items():
            setattr(k, feld, wert)
        db.add(k)
        await db.commit()
        self.gh.issues.append(_issue(nr, labels=labels))
        return k

    async def test_agent_schiebt_nicht_nach_bereit_und_verwirft_nicht(self):
        async with self.Session() as db:
            await self._karte(db, "k1", "eingang")
            for spalte in ("bereit", "verworfen"):
                with self.assertRaises(HTTPException) as e:
                    await api.karte_aendern("p1", "k1", api.KarteAendern(spalte=spalte), user=agent("a1"), db=db,
                                            redis=None)
                self.assertEqual(e.exception.status_code, 409)
            self.assertEqual((await db.get(ProjektKarte, "k1")).spalte, "eingang")
            # Ein Mensch darf beides.
            r = await api.karte_aendern("p1", "k1", api.KarteAendern(spalte="bereit"), user=MITGLIED, db=db,
                                        redis=None)
            self.assertEqual(r["spalte"], "bereit")

    async def test_agent_vergibt_nur_aus_bereit(self):
        async with self.Session() as db:
            await self._karte(db, "e", "eingang")
            await self._karte(db, "t", "triage")
            await self._karte(db, "b", "bereit")
            router = _Router(db)
            for kid in ("e", "t"):
                with self.assertRaises(HTTPException) as e:
                    await api.karte_vergeben("p1", kid, api.Vergeben(), request=None, router_=router,
                                             user=agent("a1"), db=db)
                self.assertEqual(e.exception.status_code, 409)
            r = await api.karte_vergeben("p1", "b", api.Vergeben(), request=None, router_=router,
                                         user=agent("a1"), db=db)
            self.assertEqual((r["spalte"], r["agent_id"]), ("in_arbeit", "a1"))
            # Der Mensch darf direkt aus der Inbox vergeben — das ist seine Freigabe.
            r = await api.karte_vergeben("p1", "e", api.Vergeben(agent_id="a2"), request=None,
                                         router_=router, user=MITGLIED, db=db)
            self.assertEqual(r["spalte"], "in_arbeit")

    async def test_agent_setzt_triage_tags_und_gibt_die_uebernahme_frei(self):
        async with self.Session() as db:
            await self._issue_karte(db, 21, "in_arbeit", labels=["status/in-arbeit", "status/ready"],
                                    task_id="t21", agent_id="a1")
            k = (await self._karten(db))[("issue", 21)]
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                r = await api.karte_aendern("p1", k.id, api.KarteAendern(spalte="triage", notiz="Welche API?"),
                                            user=agent("a1"), db=db, redis=None)
            self.assertEqual((r["spalte"], r["task_id"], r["notiz"]), ("triage", "t21", "Welche API?"))
        # Im Repo übliches Triage-Tag gesetzt, In-Arbeit und Bereit entfernt.
        self.assertIn(("+", 21, ("agent:blocked",)), self.gh.geschrieben)
        self.assertIn(("-", 21, "status/in-arbeit"), self.gh.geschrieben)
        self.assertIn(("-", 21, "status/ready"), self.gh.geschrieben)
        kommentar = self.gh.kommentare[-1][1]
        self.assertTrue(kommentar.startswith("<!-- ai-employee:release"))
        self.assertIn("Rückfrage offen", kommentar)
        async with self.Session() as db:
            self.assertEqual((await db.get(ProjektKarte, k.id)).labels, ["agent:blocked"])

    async def test_fremde_karte_im_lauf_setzt_der_agent_nicht_auf_triage(self):
        async with self.Session() as db:
            await self._karte(db, "k1", "in_arbeit", task_id="t1", agent_id="a2")
            with self.assertRaises(HTTPException) as e:
                await api.karte_aendern("p1", "k1", api.KarteAendern(spalte="triage"), user=agent("a1"), db=db,
                                        redis=None)
            self.assertEqual(e.exception.status_code, 409)

    async def test_mensch_gibt_frei_setzt_bereit_tag_und_nimmt_triage_weg(self):
        async with self.Session() as db:
            await self._issue_karte(db, 22, "triage", labels=["agent:blocked", "bug"], task_id="t22",
                                    agent_id="a1")
            k = (await self._karten(db))[("issue", 22)]
            with patch.object(projekt_git, "zugang", return_value=("t", self.gh)):
                r = await api.karte_aendern("p1", k.id, api.KarteAendern(spalte="bereit"), user=MITGLIED, db=db,
                                            redis=None)
            self.assertEqual((r["spalte"], r["task_id"], r["agent_id"]), ("bereit", None, None))
            self.assertEqual(r["labels"], ["bug", "status/ready"])
        self.assertIn(("+", 22, ("status/ready",)), self.gh.geschrieben)
        self.assertIn(("-", 22, "agent:blocked"), self.gh.geschrieben)

    async def test_freigabe_ohne_schluessel_bleibt_in_triage(self):
        """Käme die Freigabe nicht ins Repo, holte der nächste Abgleich die Karte zurück."""
        async with self.Session() as db:
            await self._issue_karte(db, 23, "triage", labels=["status/triage"])
            kid = (await self._karten(db))[("issue", 23)].id
            with patch.object(projekt_git, "zugang", return_value=("", self.gh)), \
                    self.assertRaises(HTTPException) as e:
                await api.karte_aendern("p1", kid, api.KarteAendern(spalte="bereit"), user=MITGLIED, db=db,
                                        redis=None)
            self.assertEqual(e.exception.status_code, 424)
            self.assertEqual((await db.get(ProjektKarte, kid)).spalte, "triage")
        self.assertFalse(getattr(self.gh, "geschrieben", []))


class LaufUndTakt(Basis):
    async def test_lauf_ende_laesst_triage_karte_stehen(self):
        async with self.Session() as db:
            await self._karte(db, "k1", "triage", task_id="t1", agent_id="a1")
            for status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                t = Task(id="t1", title="t", prompt="p", agent_id="a1", status=status, metadata_={"karte_id": "k1"})
                await projekte.lauf_beendet(db, t)
                self.assertEqual((await db.get(ProjektKarte, "k1")).spalte, "triage")

    async def test_takt_wird_von_triage_nicht_geweckt_und_meldet_einmal(self):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.models.projekt_nachricht import ProjektNachricht
        from app.models.schedule import Schedule

        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.lead_agent_id, team.takt_minuten = "a1", 30
            await projekt_takt.abgleichen(db, team)
            await db.commit()
            sched = await db.get(Schedule, team.takt_schedule_id)
            sched.last_run_at = datetime.now(timezone.utc) + timedelta(seconds=5)
            await self._karte(db, "q1", "triage", extern_nr=12, quelle="issue")
            grund, _ = await projekt_takt.pruefen(db, sched)
            self.assertTrue(grund)  # Triage ist keine Arbeit für den Takt
            await db.commit()
            await projekt_takt.pruefen(db, sched)
            await db.commit()
            texte = [n.text for n in (await db.execute(select(ProjektNachricht))).scalars()]
        meldungen = [t for t in texte if t.startswith("Wartet auf dich:")]
        self.assertEqual(len(meldungen), 1)
        self.assertIn("#12", meldungen[0])

    def test_auftrag_nennt_rueckfrage_und_selbst_mergen(self):
        k = ProjektKarte(id="k1", team_id="p1", titel="T", text="x", quelle="issue", extern_nr=9)
        auftrag = projekte.karten_auftrag(k, "P")
        self.assertIn("„triage“", auftrag)
        self.assertIn("freigeben darf nur ein Mensch", auftrag)
        self.assertIn("Selbst mergen", auftrag)
        self.assertIn("headSha", auftrag)
        takt = projekt_takt.takt_auftrag(Team(id="p1", name="P"))
        self.assertIn("Freigeben (nach „bereit“) darf nur ein Mensch", takt)
