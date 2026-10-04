"""Dashboard-Kennzahlen vom Server — inklusive Chat (#896).

Markttest: „Erledigt 0 / Kosten 0,00 €" nach einem Tag voller Chat-Aufträge. Das
Dashboard zählte im Browser nur Aufgaben; ein Chat-Auftrag legt aber keine an.
Jetzt kommen die Kennzahlen aus ``/analytics/overview`` (30 Tage): Erledigt =
fertige Aufgaben + Chat-Antworten, Kosten = Aufgaben + Chat, nutzergetrennt.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import analytics
from app.models.agent import Agent, AgentState
from app.models.chat_message import ChatMessage
from app.models.skill import SkillTaskUsage
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.user import UserRole

JETZT = datetime.now(timezone.utc)


class Kennzahlen(unittest.IsolatedAsyncioTestCase):
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
            for model in (Agent, Task, ChatMessage, KostenHistorie, TaskRating, SkillTaskUsage):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                Agent(id="a1", name="Eigener", state=AgentState.IDLE, user_id="ua", config={}),
                Agent(id="b1", name="Fremder", state=AgentState.IDLE, user_id="ub", config={}),
                # Nur Chat — wie im Markttest
                ChatMessage(agent_id="a1", session_id="s", message_id="m1", role="user",
                            content="Bitte ...", timestamp=JETZT),
                ChatMessage(agent_id="a1", session_id="s", message_id="m1", role="assistant",
                            content="Erledigt.", cost_usd=0.40, timestamp=JETZT),
                # Fremder Agent mit Aufgabe und Chat
                Task(id="t1", title="t", prompt="p", status=TaskStatus.COMPLETED, agent_id="b1",
                     cost_usd=3.0, created_at=JETZT),
                ChatMessage(agent_id="b1", session_id="s", message_id="m2", role="assistant",
                            content="x", cost_usd=1.0, timestamp=JETZT),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def overview(self, user, sichtbar):
        async with self.Session() as db:
            with patch("app.api.analytics.visible_agent_ids", AsyncMock(return_value=sichtbar)):
                return await analytics.get_overview(days=30, user=user, db=db)

    async def test_chat_zaehlt_als_erledigt_und_kostet(self):
        out = await self.overview(SimpleNamespace(id="ua", role=UserRole.MEMBER), {"a1"})
        self.assertGreaterEqual(out["done_total"], 1)
        self.assertEqual(out["chat_replies"], 1)
        self.assertEqual(out["completed_tasks"], 0)
        self.assertAlmostEqual(out["total_cost_usd"], 0.40)

    async def test_mitglied_sieht_keine_fremden_kosten(self):
        out = await self.overview(SimpleNamespace(id="ua", role=UserRole.MEMBER), {"a1"})
        self.assertAlmostEqual(out["total_chat_cost_usd"], 0.40)
        self.assertAlmostEqual(out["total_task_cost_usd"], 0.0)

    async def test_admin_sieht_alles(self):
        out = await self.overview(SimpleNamespace(id="adm", role=UserRole.ADMIN), None)
        self.assertAlmostEqual(out["total_cost_usd"], 4.40)
        self.assertEqual(out["done_total"], 3)
        self.assertAlmostEqual(sum(d["cost"] for d in out["daily_tasks"]), 4.40)


if __name__ == "__main__":
    unittest.main()
