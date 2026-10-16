"""Finanzen: Kosten je Monat/Projekt/Agent, Projektbudget, Historie mit Projekt, CSV.

Gegen eine echte (SQLite-)Datenbank und die echte Sichtbarkeit (ownership.visible_agent_ids).
"""

from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import finanzen as api
from app.core import budget, finanzen, kosten
from app.core.task_router import TaskRouter
from app.models.agent_access import AgentAccess
from app.models.chat_message import ChatMessage
from app.models.kosten_historie import KostenHistorie
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.team import Team
from app.models.user import UserRole
from tests.test_projekte import BETRACHTER, JETZT, MITGLIED, Basis

B = SimpleNamespace(id="ub", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)
VORMONAT = kosten.monatsbeginn() - timedelta(days=3)


class _Finanzen(Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.engine.begin() as conn:
            for model in (ChatMessage, Notification, AgentAccess):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        async with self.Session() as db:
            db.add_all([
                Task(id="t1", title="Karte", prompt="p", agent_id="a1", team_id="p1", status=TaskStatus.COMPLETED,
                     cost_usd=1.5, input_tokens=100, output_tokens=40, created_at=JETZT),
                Task(id="t2", title="frei", prompt="p", agent_id="a2", status=TaskStatus.COMPLETED,
                     cost_usd=0.5, created_at=JETZT),
                Task(id="t3", title="alt", prompt="p", agent_id="a1", team_id="p1", status=TaskStatus.COMPLETED,
                     cost_usd=2.0, created_at=VORMONAT),
                Task(id="tb", title="fremd", prompt="p", agent_id="b1", team_id="p2", status=TaskStatus.COMPLETED,
                     cost_usd=9.0, created_at=JETZT),
                ChatMessage(agent_id="a1", message_id="m1", role="assistant", cost_usd=0.25,
                            input_tokens=10, output_tokens=5, timestamp=JETZT),
            ])
            await db.commit()


class Uebersicht(_Finanzen):
    async def test_monat_projekte_agenten_nur_eigene(self):
        async with self.Session() as db:
            u = await finanzen.uebersicht(db, MITGLIED, 3)
        self.assertEqual(u["summe"]["monat_usd"], 2.25)  # 1,5 + 0,5 + Chat 0,25 — nicht die 9 von B
        self.assertEqual(u["summe"]["chat_usd"], 0.25)
        self.assertEqual(u["summe"]["vormonat_usd"], 2.0)
        self.assertEqual(u["summe"]["ohne_projekt_aufgaben_usd"], 0.5)
        self.assertEqual([p["id"] for p in u["je_projekt"]], ["p1"])  # p2 gehört B
        p1 = u["je_projekt"][0]
        self.assertEqual((p1["monat_usd"], p1["vormonat_usd"], p1["gesamt_usd"]), (1.5, 2.0, 3.5))
        self.assertEqual((p1["tokens_ein"], p1["tokens_aus"]), (100, 40))
        self.assertTrue(p1["darf_budget"])
        agenten = {a["id"]: a for a in u["je_agent"]}
        self.assertEqual(set(agenten), {"a1", "a2"})
        self.assertEqual((agenten["a1"]["aufgaben_usd"], agenten["a1"]["chat_usd"]), (1.5, 0.25))
        self.assertEqual((agenten["a1"]["tokens_ein"], agenten["a1"]["tokens_aus"]), (110, 45))
        self.assertEqual(len(u["verlauf"]), 3)
        self.assertEqual(u["verlauf"][-1]["gesamt_usd"], 2.25)

    async def test_summe_geht_auf_auch_mit_geloeschtem_projekt(self):
        async with self.Session() as db:
            db.add(Task(id="tx", title="alt", prompt="p", agent_id="a2", team_id="weg", status=TaskStatus.COMPLETED,
                        cost_usd=0.75, created_at=JETZT))
            await db.commit()
            u = await finanzen.uebersicht(db, MITGLIED, 1)
        s = u["summe"]
        self.assertEqual(s["andere_projekte_aufgaben_usd"], 0.75)
        projekte = sum(p["monat_usd"] for p in u["je_projekt"])
        self.assertAlmostEqual(projekte + s["ohne_projekt_aufgaben_usd"] + s["andere_projekte_aufgaben_usd"], s["aufgaben_usd"])

    async def test_admin_sieht_nur_eigenes(self):
        """Nutzerbezogen: auch der Admin sieht hier weder fremde Projekte noch fremde Kosten."""
        async with self.Session() as db:
            u = await finanzen.uebersicht(db, ADMIN, 1)
        self.assertEqual(u["je_projekt"], [])
        self.assertEqual(u["je_agent"], [])
        self.assertEqual(u["summe"]["monat_usd"], 0)

    async def test_geloeschte_aufgabe_behaelt_ihr_projekt(self):
        async with self.Session() as db:
            bedingung = Task.id == "t1"
            await kosten.verdichten(db, kosten.QUELLE_AUFGABEN, bedingung)
            await db.execute(delete(Task).where(bedingung))
            await db.commit()
            hist = (await db.execute(select(KostenHistorie))).scalars().all()
            self.assertEqual([(h.team_id, h.betrag_usd) for h in hist], [("p1", 1.5)])
            self.assertEqual((await kosten.je_projekt(db, ["p1"], seit=kosten.monatsbeginn()))["p1"], 1.5)
            self.assertEqual((await kosten.kosten(db, kosten.Bereich.projekt("p1"),
                                                  seit=kosten.monatsbeginn())).gesamt, 1.5)

    async def test_csv_entschaerft_formeln(self):
        async with self.Session() as db:
            (await db.get(Team, "p1")).name = "=HYPERLINK(1)"
            await db.commit()
            text = finanzen.als_csv(await finanzen.uebersicht(db, MITGLIED, 2))
        self.assertIn("'=HYPERLINK(1)", text)
        self.assertTrue(text.startswith("Art;Name;Monat"))


class Projektbudget(_Finanzen):
    def _router(self, db):
        r = TaskRouter.__new__(TaskRouter)
        r.db = db
        return r

    async def test_erschoepftes_budget_lehnt_laeufe_des_projekts_ab(self):
        async with self.Session() as db:
            (await db.get(Team, "p1")).budget_usd = 1.0
            await db.commit()
            with self.assertRaises(ValueError) as e:
                await self._router(db)._projekt_budget_durchsetzen("a1", {"team_id": "p1"}, None)
            self.assertIn("Monatsbudget des Projekts", str(e.exception))
            self.assertNotIn("$", str(e.exception))  # keine Beträge im Hinweis an den Auftraggeber
            # Unterauftrag eines Projektmitglieds erbt das Projekt — und die Sperre.
            with self.assertRaises(ValueError):
                await self._router(db)._projekt_budget_durchsetzen("a2", None, "t1")
            # Ein Nicht-Mitglied erbt nichts und arbeitet weiter; ohne Projekt sowieso.
            await self._router(db)._projekt_budget_durchsetzen("b1", None, "t1")
            await self._router(db)._projekt_budget_durchsetzen("a1", None, None)
            meldungen = (await db.execute(select(Notification))).scalars().all()
        self.assertEqual(len(meldungen), 1)  # einmal je Projekt und Monat
        self.assertEqual(meldungen[0].action_url, "/finanzen?projekt=p1")

    async def test_meldung_nur_an_den_ersteller(self):
        """Gemischtes Projekt: der abgewiesene Agent gehört B — die Meldung geht an A (Ersteller)."""
        async with self.Session() as db:
            t = await db.get(Team, "p1")
            t.budget_usd, t.member_agent_ids = 1.0, ["b1", "a2"]
            await db.commit()
            with self.assertRaises(ValueError):
                await self._router(db)._projekt_budget_durchsetzen("b1", {"team_id": "p1"}, None)
            meldungen = (await db.execute(select(Notification))).scalars().all()
        self.assertEqual([m.agent_id for m in meldungen], ["a2"])  # Agent von A, nicht b1
        self.assertIn("von", meldungen[0].message)  # Beträge nur in der Meldung an den Ersteller

    async def test_ohne_agent_des_erstellers_keine_meldung(self):
        async with self.Session() as db:
            t = await db.get(Team, "p1")
            t.budget_usd, t.member_agent_ids = 1.0, ["b1"]
            await db.commit()
            with self.assertRaises(ValueError):
                await self._router(db)._projekt_budget_durchsetzen("b1", {"team_id": "p1"}, None)
            self.assertEqual((await db.execute(select(Notification))).scalars().all(), [])

    async def test_budget_mit_luft_und_ohne_budget(self):
        async with self.Session() as db:
            self.assertEqual((await budget.projekt_budget_pruefen(db, "p1")).art, budget.ERLAUBEN)
            (await db.get(Team, "p1")).budget_usd = 5.0
            await db.commit()
            self.assertEqual((await budget.projekt_budget_pruefen(db, "p1")).art, budget.ERLAUBEN)

    async def test_budget_setzt_nur_der_ersteller(self):
        async with self.Session() as db:
            r = await api.projekt_budget_setzen("p1", api.ProjektBudget(budget_usd=25), user=MITGLIED, db=db)
            self.assertEqual(r["budget_usd"], 25)
            for wer, code in ((B, 404), (ADMIN, 404), (BETRACHTER, 403)):
                with self.assertRaises(HTTPException) as e:
                    await api.projekt_budget_setzen("p1", api.ProjektBudget(budget_usd=1), user=wer, db=db)
                self.assertEqual(e.exception.status_code, code, wer)
            (await db.get(Team, "p1")).geteilt_mit = ["ub"]
            await db.commit()
            with self.assertRaises(HTTPException) as e:  # freigegeben sieht, setzt aber nicht
                await api.projekt_budget_setzen("p1", api.ProjektBudget(budget_usd=1), user=B, db=db)
            self.assertEqual(e.exception.status_code, 403)
            await api.projekt_budget_setzen("p1", api.ProjektBudget(budget_usd=0), user=MITGLIED, db=db)
            self.assertIsNone((await db.get(Team, "p1")).budget_usd)

    async def test_faelliger_termin_faellt_bei_aufgebrauchtem_budget_aus(self):
        import json

        from app.models.meeting_room import MeetingRoom
        from app.models.schedule import Schedule
        from app.services.scheduler_service import SchedulerService

        async with self.engine.begin() as conn:
            await conn.run_sync(MeetingRoom.metadata.create_all, tables=[MeetingRoom.__table__])
        async with self.Session() as db:
            (await db.get(Team, "p1")).budget_usd = 1.0
            sched = Schedule(id="s1", name="[Projekt-Termin] Daily", interval_seconds=0, team_id="p1", next_run_at=JETZT,
                             prompt="__meeting__:" + json.dumps({"team_id": "p1", "agent_ids": ["a1", "a2"]}))
            db.add(sched)
            await db.commit()
            dienst = SchedulerService.__new__(SchedulerService)
            await dienst._execute_meeting_schedule(db, sched)
            self.assertEqual((await db.execute(select(MeetingRoom))).scalars().all(), [])

    async def test_board_nennt_die_monatskosten(self):
        from app.core import projekte

        async with self.Session() as db:
            b = await projekte.board(db, "p1", ["a1", "a2"])
        self.assertEqual(b["kennzahlen"]["kosten_monat_usd"], 1.5)


class Monate(Basis):
    async def test_monatsgrenzen_ueber_den_jahreswechsel(self):
        from datetime import datetime, timezone

        m = finanzen.monate(3, datetime(2026, 1, 15, tzinfo=timezone.utc))
        self.assertEqual([x[0] for x in m], ["2025-11", "2025-12", "2026-01"])
        self.assertEqual(m[1][2], m[2][1])
