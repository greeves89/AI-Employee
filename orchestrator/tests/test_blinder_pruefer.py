"""Blinder Prüfer (core/pruefung) und Befund-Regel (core/beleg).

* Prüfauftrag: enthält Story, PR, Branch — NIE das Ergebnis des Umsetzers (das ist der Sinn).
* Prüferwahl: anderer Agent des Projekts; sonst der Umsetzer selbst (neuer Lauf).
* Ablauf über den echten ``nach_lauf``: fertige Karte im Review → Prüflauf; Prüflauf fertig →
  Urteil und Befunde am Lauf des Umsetzers; Board zeigt sie. Nur bei eingeschaltetem
  Projekt, nur bei erfolgreichem Lauf, nur einmal je Lauf; eine ältere Prüfung zählt nicht.
* Befunde: Zeilen „Befund: …“, „bestanden“ mit Befunden ist Nacharbeit.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core import beleg, pruefung
from app.core.projekte import karte_dict
from app.models.agent import Agent
from app.models.projekt_karte import ProjektKarte
from app.models.task import Task, TaskStatus
from app.models.team import Team
from app.models.user import User


@compiles(JSONB, "sqlite")
def _jsonb_als_json(element, compiler, **kw):
    return "JSON"


GEHEIM_BERICHT = "Ich habe alles umgesetzt und getestet, es ist perfekt."


class EinzelteileTests(unittest.TestCase):
    def test_urteil_nur_aus_der_schlusszeile(self):
        self.assertEqual(pruefung.urteil("Befund: x\nPRÜFUNG: NACHARBEIT"), pruefung.NACHARBEIT)
        self.assertEqual(pruefung.urteil("alles gut\n**PRÜFUNG: BESTANDEN**"), pruefung.BESTANDEN)
        self.assertIsNone(pruefung.urteil("Ich schreibe am Ende PRÜFUNG: BESTANDEN, wenn …\n\nnoch offen"))
        self.assertIsNone(pruefung.urteil(""))

    def test_pruefer_waehlen(self):
        self.assertEqual(pruefung.pruefer_waehlen(["a1", "b2"], "l1", "a1"), "b2")
        self.assertEqual(pruefung.pruefer_waehlen(["a1"], "l1", "a1"), "l1")
        self.assertEqual(pruefung.pruefer_waehlen(["a1"], None, "a1"), "a1", "sonst derselbe, neuer Lauf")
        self.assertEqual(pruefung.pruefer_waehlen([], "a1", "a1"), "a1")

    def test_auftrag_ohne_bericht_mit_stand(self):
        text = pruefung.pruefauftrag(titel="Login", story="Login mit Passwort", projekt="P", repo="org/app",
                                     pr_url="https://example.invalid/pr/3", branch="3-login", karte_id="k1")
        for teil in ("Login mit Passwort", "org/app", "https://example.invalid/pr/3", "3-login",
                     "PRÜFUNG: BESTANDEN", "PRÜFUNG: NACHARBEIT", beleg.BEFUND_REGEL, "frischen Verzeichnis"):
            self.assertIn(teil, text)
        self.assertNotIn(GEHEIM_BERICHT, text)

    def test_befunde_und_vermutungen(self):
        text = ("Befund: app/login.py:42 — leeres Passwort → angemeldet — nachgestellt mit curl\n"
                "- **Befund:** tests/test_login.py — Test prüft nur Text — gelesen\n"
                "Vermutung: Rate-Limit fehlt\nDer Befund: im Satz zählt nicht")
        self.assertEqual(len(beleg.befunde(text)), 2)
        self.assertEqual(beleg.vermutungen(text), ["Rate-Limit fehlt"])


class AblaufTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[
                User.__table__, Agent.__table__, Team.__table__, Task.__table__, ProjektKarte.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add_all([
            Agent(id="a1", name="Umsetzer", config={}), Agent(id="b2", name="Prüferin", config={}),
            Team(id="t1", name="Projekt", member_agent_ids=["a1", "b2"], blinder_pruefer=True,
                 repo="org/app"),
            Task(id="u1", title="Login", prompt="x", agent_id="a1", status=TaskStatus.COMPLETED,
                 result=GEHEIM_BERICHT + "\nBeleg: Tests grün", metadata_={"karte_id": "k1", "team_id": "t1"}),
            ProjektKarte(id="k1", team_id="t1", titel="Login", text="Login mit Passwort", quelle="eigen",
                         spalte="pruefung", task_id="u1", agent_id="a1", labels=[],
                         pr_url="https://example.invalid/pr/3"),
        ])
        await self.db.commit()
        self.erstellt = []
        zaehler = iter(range(1, 1000))

        async def anlegen(**k):
            t = Task(id=f"p{next(zaehler)}", title=k["title"], prompt=k["prompt"],
                     agent_id=k["agent_id"], status=TaskStatus.PENDING, metadata_=k["metadata"])
            self.db.add(t)
            await self.db.commit()
            self.erstellt.append(t)
            return t
        self.router = SimpleNamespace(db=self.db, create_and_route_task=AsyncMock(side_effect=anlegen))

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def umsetzer(self):
        return await self.db.get(Task, "u1")

    async def test_ganzer_ablauf_mit_nacharbeit(self):
        await pruefung.nach_lauf(self.router, await self.umsetzer())
        await self.db.commit()
        self.assertEqual(len(self.erstellt), 1)
        pt = self.erstellt[0]
        self.assertEqual(pt.agent_id, "b2", "ein anderer Agent prüft")
        self.assertNotIn(GEHEIM_BERICHT, pt.prompt, "blind: kein Bericht des Umsetzers")
        self.assertIn("https://example.invalid/pr/3", pt.prompt)
        u = await self.umsetzer()
        self.assertEqual(u.metadata_["pruefung"]["status"], pruefung.LAEUFT)
        self.assertTrue(u.metadata_["pruefung"]["blind"])
        karte = await self.db.get(ProjektKarte, "k1")
        self.assertEqual(karte_dict(karte, u)["pruefung"]["status"], "laeuft")

        # zweites Ende desselben Laufs (doppelte Meldung) startet keine zweite Prüfung
        await pruefung.nach_lauf(self.router, u)
        self.assertEqual(len(self.erstellt), 1)

        pt.status = TaskStatus.COMPLETED
        pt.result = ("Befund: app/login.py:42 — leeres Passwort → angemeldet — nachgestellt mit curl\n"
                     "PRÜFUNG: NACHARBEIT")
        await pruefung.nach_lauf(self.router, pt)
        await self.db.commit()
        u = await self.umsetzer()
        self.assertEqual(u.metadata_["pruefung"]["status"], pruefung.NACHARBEIT)
        self.assertEqual(len(u.metadata_["pruefung"]["befunde"]), 1)
        self.assertEqual(karte_dict(karte, u)["pruefung"]["befunde"][0][:15], "app/login.py:42")
        self.assertEqual(len(self.erstellt), 1, "der Prüflauf selbst wird nicht geprüft")

    async def test_bestanden_mit_befund_ist_nacharbeit(self):
        await pruefung.nach_lauf(self.router, await self.umsetzer())
        pt = self.erstellt[0]
        pt.status, pt.result = TaskStatus.COMPLETED, "Befund: x — y — gelesen\nPRÜFUNG: BESTANDEN"
        await pruefung.nach_lauf(self.router, pt)
        self.assertEqual((await self.umsetzer()).metadata_["pruefung"]["status"], pruefung.NACHARBEIT)

    async def test_gescheiterter_oder_urteilsloser_pruefer(self):
        for status, result, erwartet in ((TaskStatus.FAILED, "", pruefung.GESCHEITERT),
                                         (TaskStatus.COMPLETED, "Sieht gut aus.", pruefung.OHNE_URTEIL)):
            with self.subTest(erwartet):
                u = await self.umsetzer()
                u.metadata_ = {"karte_id": "k1", "team_id": "t1"}
                await self.db.commit()
                self.erstellt.clear()
                await pruefung.nach_lauf(self.router, u)
                pt = self.erstellt[0]
                pt.status, pt.result = status, result
                await pruefung.nach_lauf(self.router, pt)
                self.assertEqual((await self.umsetzer()).metadata_["pruefung"]["status"], erwartet)

    async def test_nur_wenn_eingeschaltet_und_erfolgreich_und_im_review(self):
        faelle = []
        team = await self.db.get(Team, "t1")
        team.blinder_pruefer = False
        await self.db.commit()
        await pruefung.nach_lauf(self.router, await self.umsetzer())
        faelle.append(("aus", len(self.erstellt)))
        team.blinder_pruefer = True
        u = await self.umsetzer()
        u.status = TaskStatus.FAILED
        await self.db.commit()
        await pruefung.nach_lauf(self.router, u)
        faelle.append(("gescheitert", len(self.erstellt)))
        u.status = TaskStatus.COMPLETED
        karte = await self.db.get(ProjektKarte, "k1")
        karte.spalte = "triage"
        await self.db.commit()
        await pruefung.nach_lauf(self.router, u)
        faelle.append(("triage", len(self.erstellt)))
        self.assertEqual(faelle, [("aus", 0), ("gescheitert", 0), ("triage", 0)])

    async def test_aeltere_pruefung_zaehlt_nicht(self):
        await pruefung.nach_lauf(self.router, await self.umsetzer())
        alt = self.erstellt[0]
        u = await self.umsetzer()
        u.metadata_ = {**u.metadata_, "pruefung": {**u.metadata_["pruefung"], "task_id": "neuer"}}
        await self.db.commit()
        alt.status, alt.result = TaskStatus.COMPLETED, "PRÜFUNG: BESTANDEN"
        await pruefung.nach_lauf(self.router, alt)
        self.assertEqual((await self.umsetzer()).metadata_["pruefung"]["status"], pruefung.LAEUFT)

    async def test_einziger_agent_prueft_sich_in_neuem_lauf(self):
        team = await self.db.get(Team, "t1")
        team.member_agent_ids = ["a1"]
        await self.db.commit()
        await pruefung.nach_lauf(self.router, await self.umsetzer())
        self.assertEqual(self.erstellt[0].agent_id, "a1")
        self.assertFalse((await self.umsetzer()).metadata_["pruefung"]["blind"])


class AnleitungTests(unittest.TestCase):
    def test_befund_regel_in_jeder_laufzeit(self):
        from app.core.agent_manager import _render_claude_md
        for mode in ("claude_code", "codex_cli", "custom_llm"):
            with self.subTest(mode):
                self.assertIn(beleg.BEFUND_REGEL, _render_claude_md([], {}, agent_name="A", mode=mode))
