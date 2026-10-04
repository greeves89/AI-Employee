"""Die letzten Stellen, die Kosten selbst summierten, fragen jetzt ``core/kosten`` (#896).

Kiosk (Übersicht und Agentendetail), die mobile Zusammenfassung ``/tasks/summary``,
die Kostenzuordnung im Dashboard, die Kurzliste der Agenten und die Agentendetails
der Auswertung zählten nur Aufgaben — ein Agent, der den ganzen Tag im Chat
arbeitete, kostete dort nichts. Geprüft wird gegen eine echte (SQLite-)Datenbank
mit denselben Testdaten wie die Kostenquelle selbst.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.agent_access import AgentAccess
from app.models.task_rating import TaskRating
from app.models.user import UserRole
from tests.test_kosten_eine_quelle import KostenBasis

MITGLIED_A = SimpleNamespace(id="ua", role=UserRole.MEMBER, email="a@example.invalid")
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN, email="adm@example.invalid")

class Basis(KostenBasis):
    async def asyncSetUp(self):
        await super().asyncSetUp()

        async with self.engine.begin() as conn:
            # Postgres-Funktion der Tageskurve in den Agentendetails. Die
            # In-Memory-Datenbank hat genau eine Verbindung — dort registrieren.
            await conn.run_sync(lambda sync: sync.connection.dbapi_connection.create_function(
                "date_trunc", 2, lambda _einheit, wert: str(wert)[:10]))
            for model in (AgentAccess, TaskRating):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])

class Kiosk(Basis):
    async def test_uebersicht_heute_aufgaben_und_chat(self):
        from app.api import kiosk

        async with self.Session() as db:
            out = await kiosk.kiosk_overview(db=db)
        # Die ganze Anlage, inklusive gelöschter Agenten: 12,50 Aufgaben + 3,00 Chat.
        self.assertAlmostEqual(out["ai_spend"]["cost_usd_today"], 15.50)

    async def test_agentendetail_zaehlt_chat_mit(self):
        from app.api import kiosk

        async with self.Session() as db:
            out = await kiosk.kiosk_agent_detail("a1", db=db)
        # Seit Beginn: Aufgaben 1 + 10, Chat 0,25 + 3.
        self.assertAlmostEqual(out["cost_usd_total"], 14.25)

class MobileZusammenfassung(Basis):
    async def test_mitglied_sieht_eigene_kosten_inklusive_chat(self):
        from app.api import tasks

        async with self.Session() as db:
            out = await tasks.get_task_summary(user=MITGLIED_A, db=db)
        # a1 + a2 seit Beginn: Aufgaben 1 + 0,5 + 10, Chat 0,25 + 3 — nichts von B.
        self.assertAlmostEqual(out.total_cost_usd, 14.75)
        self.assertEqual(out.total, 3)

    async def test_admin_sieht_die_anlage(self):
        from app.api import tasks

        async with self.Session() as db:
            out = await tasks.get_task_summary(user=ADMIN, db=db)
        self.assertAlmostEqual(out.total_cost_usd, 28.50)

    async def test_agent_sieht_nur_sich(self):
        from app.api import tasks
        from app.dependencies import AgentPrincipal

        async with self.Session() as db:
            out = await tasks.get_task_summary(user=AgentPrincipal(id="b1"), db=db)
        self.assertAlmostEqual(out.total_cost_usd, 6.00)

class Kostenzuordnung(Basis):
    async def test_mitglied_rangliste_mit_chat(self):
        from app.api import tasks

        async with self.Session() as db:
            with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value={"a1", "a2"})):
                out = await tasks.get_cost_attribution(limit=5, user=MITGLIED_A, db=db)
        self.assertEqual([a.agent_id for a in out.top_agents], ["a1", "a2"])
        self.assertAlmostEqual(out.top_agents[0].total_cost_usd, 14.25)
        self.assertEqual(out.top_agents[0].task_count, 2)
        self.assertAlmostEqual(out.platform_total_usd, 14.75)

    async def test_admin_summe_inklusive_geloeschter_agenten(self):
        from app.api import tasks

        async with self.Session() as db:
            with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value=None)):
                out = await tasks.get_cost_attribution(limit=5, user=ADMIN, db=db)
        self.assertAlmostEqual(out.platform_total_usd, 28.50)
        self.assertEqual({a.agent_id for a in out.top_agents}, {"a1", "a2", "b1"})
        # Rangfolge nach Gesamtkosten: a1 14,25 > b1 6,00 > a2 0,50
        self.assertEqual([a.agent_id for a in out.top_agents], ["a1", "b1", "a2"])

class Auswertung(Basis):
    async def test_agentendetail_zaehlt_chat_mit(self):
        from app.api import analytics

        async with self.Session() as db:
            with patch.object(analytics, "visible_agent_ids", AsyncMock(return_value={"a1", "a2"})):
                out = await analytics.get_agent_detail("a1", days=1, user=MITGLIED_A, db=db)
        # Letzter Tag: Aufgabe 1,00 + Chat 0,25 (der Vormonat zählt nicht).
        self.assertAlmostEqual(out["summary"]["total_cost_usd"], 1.25)
        self.assertEqual(out["summary"]["total_tasks"], 1)

class Kurzliste(Basis):
    """``GET /agents/?lite=true`` (Kurzbefehle, Aufgabenfilter der App) meldete 0,00."""

    async def test_monatskosten_aus_der_kostenquelle(self):
        from sqlalchemy import select

        from app.api import agents
        from app.models.agent import Agent

        async with self.Session() as db:
            alle = (await db.execute(select(Agent))).scalars().all()
            verwalter = SimpleNamespace(list_agents=AsyncMock(return_value=alle))
            out = await agents.list_agents(lite=True, scope="all", room_pool=False,
                                           user=ADMIN, db=db, manager=verwalter)
        monat = {a.id: a.monthly_cost_usd for a in out.agents}
        self.assertAlmostEqual(monat["a1"], 1.25)
        self.assertAlmostEqual(monat["b1"], 6.00)

if __name__ == "__main__":
    unittest.main()
