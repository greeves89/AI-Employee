"""Eine erledigte Aufgabe muss sich löschen lassen.

Jede erledigte Aufgabe bekommt vom Reflexions-Richter eine Zeile in
``task_ratings``. Deren Fremdschlüssel löscht nicht mit, und ``DELETE /tasks/{id}``
räumte sie nicht ab — das Löschen scheiterte mit HTTP 500
(``task_ratings_task_id_fkey``). Dasselbe galt für eine Aufgabe mit Unteraufgaben
(``tasks.parent_task_id``).

SQLite prüft Fremdschlüssel nur mit ``PRAGMA foreign_keys=ON`` — ohne das bewiese
der Test nichts.
"""

import unittest
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.task_router import TaskRouter
from app.models.agent import Agent
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.task_step import TaskStep


class AufgabeLoeschenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[
                Agent.__table__, Task.__table__, TaskRating.__table__, TaskStep.__table__,
            ])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="ag1", name="Recherche", user_id="u1"))
            db.add(Task(id="t-eltern", title="Erledigt", prompt="x", agent_id="ag1",
                        status=TaskStatus.COMPLETED))
            await db.flush()
            db.add(Task(id="t-kind", title="Unteraufgabe", prompt="y", agent_id="ag1",
                        status=TaskStatus.COMPLETED, parent_task_id="t-eltern"))
            db.add(TaskRating(task_id="t-eltern", agent_id="ag1", rating=5))
            await db.commit()
        # Erst jetzt einschalten: der Agent verweist auf Tabellen, die hier fehlen.
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _router(self, db):
        return TaskRouter(db, redis=AsyncMock(), load_balancer=AsyncMock())

    async def test_fremdschluessel_greift_wirklich(self):
        # Gegenprobe: ohne das Aufräumen scheitert das Löschen wie auf der Anlage.
        async with self.Session() as db:
            await db.delete(await db.get(Task, "t-eltern"))
            with self.assertRaises(IntegrityError):
                await db.commit()

    async def test_aufgabe_mit_bewertung_und_unteraufgabe_wird_geloescht(self):
        async with self.Session() as db:
            self.assertTrue(await self._router(db).delete_task("t-eltern"))
        async with self.Session() as db:
            self.assertIsNone(await db.get(Task, "t-eltern"))
            self.assertEqual((await db.execute(select(TaskRating))).scalars().all(), [])
            kind = await db.get(Task, "t-kind")
            self.assertIsNotNone(kind, "die Unteraufgabe ist eigene Arbeit und bleibt")
            self.assertIsNone(kind.parent_task_id)


if __name__ == "__main__":
    unittest.main()
