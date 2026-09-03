"""Nutzerbeobachtung: Long-term Memory zeigte "(50 entries)", obwohl der Agent
mehr Erinnerungen hatte — der Kunde vermutete zu Recht, dass da abgeschnitten
wird, ohne dass die UI das kenntlich macht.

Ursache: ``GET /memory/agents/{id}`` hat ``total`` und die Kategorie-Zahlen aus
der bereits auf ``limit`` (Default 50) gekappten Seite selbst berechnet
(``len(memories)`` bzw. ein Zaehl-Loop ueber genau diese Seite) — "(50 entries)"
bedeutete also immer nur "Seitengroesse", egal ob der Agent wirklich 50 oder
500 Erinnerungen hatte. Jetzt kommen ``total``/``categories`` aus echten
COUNT(*)-Abfragen ueber ALLE Erinnerungen, unabhaengig von ``limit``/``offset``,
und ``has_more`` erlaubt der Oberflaeche, ehrlich nachzuladen statt still
abzuschneiden.
"""

import unittest

from app.api import memory as api
from app.models.agent import Agent, AgentState
from app.models.memory import AgentMemory, AgentMemoryLink, AgentMemoryTag
from app.models.user import UserRole
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from types import SimpleNamespace


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


class TrueCountsBeyondThePageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, AgentMemory, AgentMemoryTag, AgentMemoryLink):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Agent", state=AgentState.RUNNING, user_id="u1", config={}))
            # 70 "learning" + 5 "fact" — more than the default page size of 50.
            for i in range(70):
                db.add(AgentMemory(agent_id="a1", category="learning", key=f"l{i}",
                                    content=f"lesson {i}", importance=3))
            for i in range(5):
                db.add(AgentMemory(agent_id="a1", category="fact", key=f"f{i}",
                                    content=f"fact {i}", importance=3))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _owner():
        return SimpleNamespace(id="u1", role=UserRole.MEMBER, email="u1@example.test")

    async def test_total_reflects_all_memories_not_just_the_page(self):
        async with self.Session() as db:
            out = await api.list_agent_memories("a1", category=None, limit=50, offset=0, user=self._owner(), db=db)
        self.assertEqual(out["total"], 75)
        self.assertEqual(len(out["memories"]), 50)  # still page-limited
        self.assertTrue(out["has_more"])

    async def test_category_counts_are_not_capped_by_the_page(self):
        """The old code only counted categories WITHIN the 50-row page — since
        order-by favours importance/recency, a smaller category could easily
        be undercounted or vanish from the chip entirely."""
        async with self.Session() as db:
            out = await api.list_agent_memories("a1", category=None, limit=50, offset=0, user=self._owner(), db=db)
        self.assertEqual(out["categories"]["learning"], 70)
        self.assertEqual(out["categories"]["fact"], 5)

    async def test_filtered_total_is_scoped_to_that_category(self):
        async with self.Session() as db:
            out = await api.list_agent_memories("a1", category="fact", limit=50, offset=0, user=self._owner(), db=db)
        self.assertEqual(out["total"], 5)
        self.assertFalse(out["has_more"])
        self.assertEqual(len(out["memories"]), 5)

    async def test_offset_pages_through_without_duplicates_or_gaps(self):
        seen_ids = set()
        offset = 0
        async with self.Session() as db:
            while True:
                out = await api.list_agent_memories(
                    "a1", category="learning", offset=offset, limit=30,
                    user=self._owner(), db=db,
                )
                ids = [m["id"] for m in out["memories"]]
                self.assertFalse(seen_ids & set(ids), "page overlap")
                seen_ids |= set(ids)
                offset += len(ids)
                if not out["has_more"]:
                    break
        self.assertEqual(len(seen_ids), 70)

    async def test_small_agent_reports_no_more_pages(self):
        async with self.Session() as db:
            out = await api.list_agent_memories("a1", category="fact", limit=50, offset=0,
                                                  user=self._owner(), db=db)
        self.assertFalse(out["has_more"])


if __name__ == "__main__":
    unittest.main()
