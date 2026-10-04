"""Health-Seite widerspruchsfrei (#896).

Markttest: oben „13 Agents registriert", darunter „Keine Agents gefunden",
„Invalid Date" beim letzten Selbsttest und Kosten nur aus Aufgaben. Ursache:
Die Seite las Felder, die der Server nie lieferte (``agents``, ``created_at``).

Jetzt liefert ``/health/dashboard`` die Agentenliste mit Status (nur sichtbare
Agenten), Kosten aus der einen Kostenquelle (Aufgaben + Chat), und
``/health/auto-metrics`` dieselben Kosten samt Tagesreihe.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import health
from app.models.agent import Agent, AgentState
from app.models.chat_message import ChatMessage
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.test_run import TestRun
from app.models.user import UserRole

JETZT = datetime.now(timezone.utc)
MITGLIED = SimpleNamespace(id="ua", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)


class HealthBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from app.models.kosten_historie import KostenHistorie  # #896: gelöschte Kosten
        async with self.engine.begin() as conn:
            for model in (Agent, Task, ChatMessage, KostenHistorie, TaskRating, TestRun):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                Agent(id="a1", name="Eigener", state=AgentState.IDLE, user_id="ua", config={}),
                Agent(id="a2", name="Kaputt", state=AgentState.ERROR, user_id="ua", config={}),
                Agent(id="b1", name="Fremder", state=AgentState.RUNNING, user_id="ub", config={}),
                Task(id="t1", title="t", prompt="p", status=TaskStatus.COMPLETED, agent_id="a1",
                     cost_usd=1.0, created_at=JETZT, completed_at=JETZT),
                Task(id="t2", title="t", prompt="p", status=TaskStatus.COMPLETED, agent_id="b1",
                     cost_usd=5.0, created_at=JETZT, completed_at=JETZT),
                ChatMessage(agent_id="a1", session_id="s", message_id="m1", role="assistant",
                            content="x", cost_usd=0.5, timestamp=JETZT),
                TestRun(started_at=JETZT, status="passed", total=3, passed=3),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def dashboard(self, user, sichtbar):
        async with self.Session() as db:
            with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value=sichtbar)):
                return await health.get_health_dashboard(user=user, db=db)

    async def metriken(self, user, sichtbar):
        async with self.Session() as db:
            with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value=sichtbar)):
                return await health.get_auto_metrics(days=7, user=user, db=db)


class Dashboard(HealthBasis):
    async def test_agentenliste_mit_status(self):
        out = await self.dashboard(MITGLIED, {"a1", "a2"})
        agenten = {a["id"]: a for a in out["agents"]}
        self.assertEqual(set(agenten), {"a1", "a2"}, "Mitglied sieht nur eigene Agenten")
        self.assertEqual(agenten["a1"]["state"], "idle")
        self.assertEqual(agenten["a1"]["health"], "healthy")
        self.assertEqual(agenten["a2"]["health"], "error")
        # Die Zahl oben und die Liste unten kommen aus derselben Quelle.
        self.assertEqual(len(out["agent_ratings"]), len(out["agents"]))

    async def test_letzter_lauf_hat_started_at(self):
        out = await self.dashboard(ADMIN, None)
        self.assertIsNotNone(out["latest_run"]["started_at"])

    async def test_kosten_mit_chat_und_nutzergetrennt(self):
        self.assertAlmostEqual((await self.dashboard(MITGLIED, {"a1", "a2"}))["total_cost_7d"], 1.5)
        self.assertAlmostEqual((await self.dashboard(ADMIN, None))["total_cost_7d"], 6.5)


class AutoMetriken(HealthBasis):
    async def test_kosten_mit_chat(self):
        out = await self.metriken(MITGLIED, {"a1", "a2"})
        self.assertAlmostEqual(out["total_cost_usd"], 1.5)
        self.assertAlmostEqual(out["chat_cost_usd"], 0.5)
        self.assertAlmostEqual(sum(t["cost"] for t in out["daily_cost"]), 1.5)

    async def test_fremde_kosten_bleiben_draussen(self):
        out = await self.metriken(MITGLIED, {"a1"})
        self.assertNotIn("b1", {a["agent_id"] for a in out["agents"]})
        self.assertAlmostEqual(out["total_cost_usd"], 1.5)


if __name__ == "__main__":
    unittest.main()
