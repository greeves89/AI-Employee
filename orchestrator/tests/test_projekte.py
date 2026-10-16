"""Projekte: Zuordnung der Auftraege, Board, Auftrag, Abnahme, Nacharbeit.

Gegen eine echte (SQLite-)Datenbank: die Projektzuordnung haengt an einem
Datenbank-Ereignis (before_insert), das ein Mock nicht ausloesen wuerde.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import teams as api
from app.core import projekte
from app.models.agent import Agent, AgentState
from app.models.task import Task, TaskStatus
from app.models.team import Team
from app.models.user import User, UserRole

JETZT = datetime.now(timezone.utc)
MITGLIED = SimpleNamespace(id="ua", role=UserRole.MEMBER)
BETRACHTER = SimpleNamespace(id="ua", role=UserRole.VIEWER)


class _Router:
    """Wie TaskRouter.create_and_route_task, nur ohne Versand."""

    def __init__(self, db):
        self.db = db
        self.aufrufe = []

    async def create_and_route_task(self, *, title, prompt, priority=1, agent_id=None,
                                    parent_task_id=None, metadata=None, **_):
        self.aufrufe.append({"agent_id": agent_id, "prompt": prompt, "metadata": metadata,
                             "parent_task_id": parent_task_id})
        task = Task(id=f"neu{len(self.aufrufe)}", title=title, prompt=prompt, priority=priority,
                    agent_id=agent_id, parent_task_id=parent_task_id, status=TaskStatus.QUEUED,
                    metadata_=dict(metadata or {}))
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return task


class Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001 — schon registriert
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent, Task, Team):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="ua", email="a@example.invalid", name="A", password_hash="x", role=UserRole.MEMBER),
                User(id="ub", email="b@example.invalid", name="B", password_hash="x", role=UserRole.MEMBER),
                Agent(id="a1", name="A1", state=AgentState.IDLE, user_id="ua", config={}),
                Agent(id="a2", name="A2", state=AgentState.IDLE, user_id="ua", config={}),
                Agent(id="b1", name="B1", state=AgentState.IDLE, user_id="ub", config={}),
                Team(id="p1", name="Projekt", description="Ziel", member_agent_ids=["a1", "a2"],
                     lead_agent_id=None, is_active=True),
                Team(id="p2", name="Anderes", description="", member_agent_ids=["b1"],
                     lead_agent_id="b1", is_active=True),
            ])
            await db.commit()
        self.sichtbar = patch("app.api.teams._get_user_agent_ids", new=AsyncMock(return_value=["a1", "a2"]))
        self.sichtbar.start()

    async def asyncTearDown(self):
        self.sichtbar.stop()
        await self.engine.dispose()

    def _task(self, tid, *, status=TaskStatus.COMPLETED, agent="a1", team="p1", abnahme=True, **kw):
        kw.setdefault("completed_at", JETZT if status == TaskStatus.COMPLETED else None)
        return Task(id=tid, title=f"Titel {tid}", prompt="tu was", status=status, agent_id=agent,
                    team_id=team, metadata_={"abnahme": True} if abnahme else {}, **kw)


class ProjektWirdVererbt(Basis):
    async def test_unterauftraege_erben_das_projekt(self):
        async with self.Session() as db:
            db.add(Task(id="w", title="w", prompt="p", agent_id="a1", metadata_={"team_id": "p1"}))
            await db.commit()
            db.add(Task(id="k", title="k", prompt="p", agent_id="a2", parent_task_id="w"))
            await db.commit()
            db.add(Task(id="e", title="e", prompt="p", agent_id="a1", parent_task_id="k"))
            db.add(Task(id="x", title="x", prompt="p", agent_id="a1"))
            await db.commit()
            self.assertEqual((await db.get(Task, "w")).team_id, "p1")
            self.assertEqual((await db.get(Task, "k")).team_id, "p1")
            self.assertEqual((await db.get(Task, "e")).team_id, "p1")
            self.assertIsNone((await db.get(Task, "x")).team_id)

    async def test_agent_ausserhalb_des_projekts_erbt_nicht(self):
        """Ein Mensch darf jeden Eltern-Auftrag nennen — sein fremder Agent landet trotzdem nicht im Projekt."""
        async with self.Session() as db:
            db.add(Task(id="w", title="w", prompt="p", agent_id="a1", team_id="p1"))
            await db.commit()
            db.add(Task(id="k", title="k", prompt="p", agent_id="b1", parent_task_id="w"))
            await db.commit()
            self.assertIsNone((await db.get(Task, "k")).team_id)

    async def test_ausdrueckliche_zuordnung_geht_vor(self):
        async with self.Session() as db:
            db.add(Task(id="w", title="w", prompt="p", agent_id="a1", team_id="p1"))
            await db.commit()
            db.add(Task(id="k", title="k", prompt="p", agent_id="a1", parent_task_id="w", team_id="p9"))
            await db.commit()
            self.assertEqual((await db.get(Task, "k")).team_id, "p9")


class Board(Basis):
    async def test_spalten(self):
        async with self.Session() as db:
            db.add_all([
                self._task("wartet", status=TaskStatus.QUEUED),
                self._task("laeuft", status=TaskStatus.RUNNING),
                self._task("abnahme"),
                self._task("gescheitert", status=TaskStatus.FAILED),
                self._task("abgenommen", abgenommen_at=JETZT),
                self._task("teil", abnahme=False),
                Task(id="zurueck", title="z", prompt="p", status=TaskStatus.COMPLETED, agent_id="a1",
                     team_id="p1", completed_at=JETZT,
                     metadata_={"abnahme": True, "zurueckgegeben_als": "neu1"}),
                self._task("alt", completed_at=JETZT - timedelta(days=60), abnahme=False),
                self._task("fremd", agent="b1"),
                self._task("anderes_projekt", team="p2"),
            ])
            await db.commit()
            b = await api.projekt_board("p1", user=MITGLIED, db=db)
        ids = {s: [k["id"] for k in karten] for s, karten in b["spalten"].items()}
        self.assertEqual(ids["wartet"], ["wartet"])
        self.assertEqual(ids["in_arbeit"], ["laeuft"])
        self.assertEqual(sorted(ids["zur_abnahme"]), ["abnahme", "gescheitert"])
        self.assertEqual(sorted(ids["erledigt"]), ["abgenommen", "teil", "zurueck"])
        self.assertEqual(b["kennzahlen"]["heute_fertig"], 4)  # abnahme, abgenommen, teil, zurueck
        self.assertEqual(len(b["kennzahlen"]["verlauf"]), 7)
        self.assertEqual(b["projekt"]["id"], "p1")

    async def test_fremdes_projekt_ist_gesperrt(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.projekt_board("p2", user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 403)

    async def test_uebersicht_nennt_nur_eigene_projekte(self):
        async with self.Session() as db:
            db.add(self._task("abnahme"))
            await db.commit()
            r = await api.projekte_uebersicht(user=MITGLIED, db=db)
        self.assertEqual([p["id"] for p in r["projekte"]], ["p1"])
        self.assertEqual(r["projekte"][0]["kennzahlen"]["zur_abnahme"], 1)


class Auftrag(Basis):
    async def _auftrag(self, db, **body):
        router = _Router(db)
        karte = await api.projekt_auftrag("p1", api.ProjektAuftrag(**body), request=None,
                                          router_=router, user=MITGLIED, db=db)
        return karte, router

    async def test_ohne_lead_und_mehrere_mitglieder_muss_man_waehlen(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await self._auftrag(db, text="Mach das")
        self.assertEqual(e.exception.status_code, 400)

    async def test_direkt_an_mitglied_landet_im_projekt_und_braucht_abnahme(self):
        async with self.Session() as db:
            karte, router = await self._auftrag(db, text="Bericht schreiben\nmit Details", agent_id="a2")
            task = await db.get(Task, karte["id"])
        self.assertEqual(router.aufrufe[0]["agent_id"], "a2")
        self.assertEqual(task.team_id, "p1")
        self.assertEqual(task.title, "Bericht schreiben")
        self.assertTrue(task.metadata_["abnahme"])

    async def test_fremder_agent_ist_kein_mitglied(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await self._auftrag(db, text="x", agent_id="b1")
        self.assertEqual(e.exception.status_code, 400)

    async def test_mitglied_eines_anderen_nutzers_bekommt_keinen_auftrag(self):
        """Gemischte Teams legt nur ein Admin an — ein Mitglied darf dessen fremden Agenten nicht beauftragen."""
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.member_agent_ids = ["a1", "a2", "b1"]
            await db.commit()
            with self.assertRaises(HTTPException) as e:
                await self._auftrag(db, text="x", agent_id="b1")
        self.assertEqual(e.exception.status_code, 403)

    async def test_mit_lead_geht_der_auftrag_an_den_lead(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.lead_agent_id = "a1"
            await db.commit()
            with patch("app.api.teams._team_roster", new=AsyncMock(return_value="- A1")):
                karte, router = await self._auftrag(db, text="Plane das Fest")
        aufruf = router.aufrufe[0]
        self.assertEqual(aufruf["agent_id"], "a1")
        self.assertIn("LEAD", aufruf["prompt"])
        self.assertEqual(aufruf["metadata"]["auftrag"], "Plane das Fest")
        self.assertTrue(aufruf["metadata"]["abnahme"])

    async def test_fremder_lead_eines_gemischten_projekts_bekommt_keinen_auftrag(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.member_agent_ids = ["a1", "b1"]
            team.lead_agent_id = "b1"
            await db.commit()
            with patch("app.api.teams._team_roster", new=AsyncMock(return_value="- B1")), \
                    self.assertRaises(HTTPException) as e:
                await self._auftrag(db, text="Plane das Fest")
        self.assertEqual(e.exception.status_code, 403)

    async def test_alte_team_delegation_prueft_den_lead_ebenso(self):
        async with self.Session() as db:
            team = await db.get(Team, "p1")
            team.member_agent_ids = ["a1", "b1"]
            team.lead_agent_id = "b1"
            await db.commit()
            with self.assertRaises(HTTPException) as e:
                await api.delegate_to_team("p1", api.DelegateTask(title="t", prompt="p"), request=None,
                                           router_=_Router(db), user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 403)

    async def test_betrachter_darf_nicht(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await api.projekt_auftrag("p1", api.ProjektAuftrag(text="x", agent_id="a1"), request=None,
                                          router_=_Router(db), user=BETRACHTER, db=db)
        self.assertEqual(e.exception.status_code, 403)


class Abnahme(Basis):
    async def test_abnehmen(self):
        async with self.Session() as db:
            db.add(self._task("t"))
            await db.commit()
            karte = await api.projekt_abnehmen("p1", "t", user=MITGLIED, db=db)
        self.assertEqual(karte["spalte"], "erledigt")
        self.assertIsNotNone(karte["abgenommen"])

    async def test_laufender_auftrag_kann_nicht_abgenommen_werden(self):
        async with self.Session() as db:
            db.add(self._task("t", status=TaskStatus.RUNNING))
            await db.commit()
            with self.assertRaises(HTTPException) as e:
                await api.projekt_abnehmen("p1", "t", user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 409)

    async def test_auftrag_eines_anderen_projekts_oder_nutzers_ist_unbekannt(self):
        async with self.Session() as db:
            db.add_all([self._task("anders", team="p2", agent="a1"), self._task("fremd", agent="b1")])
            await db.commit()
            for tid in ("anders", "fremd"):
                with self.assertRaises(HTTPException) as e:
                    await api.projekt_abnehmen("p1", tid, user=MITGLIED, db=db)
                self.assertEqual(e.exception.status_code, 404)

    async def test_zurueckgeben_legt_nacharbeit_an(self):
        async with self.Session() as db:
            t = self._task("t", result="Erster Entwurf")
            t.metadata_ = {"abnahme": True, "auftrag": "Schreib den Text"}
            db.add(t)
            await db.commit()
            router = _Router(db)
            karte = await api.projekt_zurueckgeben("p1", "t", api.Zurueckgeben(hinweis="Kürzer bitte"),
                                                   request=None, router_=router, user=MITGLIED, db=db)
            alt = await db.get(Task, "t")
            neu = await db.get(Task, karte["id"])
            self.assertEqual(alt.metadata_["zurueckgegeben_als"], neu.id)
            self.assertEqual(projekte.spalte(alt), "erledigt")
            self.assertEqual(neu.team_id, "p1")
            self.assertEqual(neu.parent_task_id, "t")
            self.assertEqual(neu.agent_id, "a1")
            self.assertIn("Kürzer bitte", neu.prompt)
            self.assertIn("Erster Entwurf", neu.prompt)
            self.assertIn("Schreib den Text", neu.prompt)
            self.assertIn("[EXTERNAL-DATA", neu.prompt)
            with self.assertRaises(HTTPException) as e:
                await api.projekt_zurueckgeben("p1", "t", api.Zurueckgeben(hinweis="nochmal"),
                                               request=None, router_=router, user=MITGLIED, db=db)
            self.assertEqual(e.exception.status_code, 409)
            with self.assertRaises(HTTPException) as e:
                await api.projekt_abnehmen("p1", "t", user=MITGLIED, db=db)
            self.assertEqual(e.exception.status_code, 409)

    async def test_scheitert_die_nacharbeit_bleibt_zurueckgeben_moeglich(self):
        async with self.Session() as db:
            db.add(self._task("t", result="x"))
            await db.commit()
            router = _Router(db)
            router.create_and_route_task = AsyncMock(side_effect=ValueError("Budget erschöpft"))
            with self.assertRaises(ValueError):
                await api.projekt_zurueckgeben("p1", "t", api.Zurueckgeben(hinweis="h"),
                                               request=None, router_=router, user=MITGLIED, db=db)
            alt = await db.get(Task, "t")
            await db.refresh(alt)
            self.assertNotIn("zurueckgegeben_als", alt.metadata_)


class Farbe(unittest.TestCase):
    def test_nur_bekannte_farben(self):
        self.assertEqual(api._farbe("blau"), "blau")
        self.assertIsNone(api._farbe(""))
        with self.assertRaises(HTTPException):
            api._farbe("red;background:url(x)")


class Titel(unittest.TestCase):
    def test_erste_zeile(self):
        self.assertEqual(projekte.titel_aus("\n  Hallo Welt \nmehr"), "Hallo Welt")
        self.assertEqual(len(projekte.titel_aus("x" * 200)), 80)


if __name__ == "__main__":
    unittest.main()
