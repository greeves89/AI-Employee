"""Die letzten Schritte mehrerer Aufgaben in EINER Anfrage — mit Zugriffspruefung.

29.09.2026, Kundenanlage: Jede laufende Auftrags-Kachel im Chat fragte alle 4 s
einzeln ihren ganzen Verlauf ab. Acht offene Auftraege = 120 Anfragen pro
Minute = die komplette Grenze je Nutzer; danach 429 auf jeder anderen Seite.
"""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import tasks as api
from app.models.task import Task
from app.models.task_step import TaskStep
from app.models.user import UserRole

NUTZER = SimpleNamespace(id="u1", role=UserRole.MEMBER)


class GebuendelteSchritteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Task.metadata.create_all, tables=[Task.__table__, TaskStep.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        for tid, agent in (("t1", "mein"), ("t2", "mein"), ("t3", "fremd")):
            self.db.add(Task(id=tid, title=tid, prompt="p", agent_id=agent))
            for i in range(30):
                self.db.add(TaskStep(task_id=tid, sequence=i, event_type="text",
                                     event_data={"text": f"{tid}-{i}"},
                                     timestamp=datetime.now(timezone.utc)))
        await self.db.commit()
        self.p = patch.object(api, "_get_user_agent_ids", AsyncMock(return_value=["mein"]))
        self.p.start()

    async def asyncTearDown(self):
        self.p.stop()
        await self.db.close()
        await self.engine.dispose()

    async def test_nur_sichtbare_und_nur_die_letzten(self):
        antwort = await api.get_steps_for_many(ids="t1,t2,t3,gibtsnicht", letzte=5, user=NUTZER, db=self.db)
        self.assertEqual(sorted(antwort["tasks"]), ["t1", "t2"])  # fremde Aufgabe fehlt
        schritte = antwort["tasks"]["t1"]
        self.assertEqual([s["sequence"] for s in schritte], [25, 26, 27, 28, 29])  # aufsteigend, die letzten

    async def test_leere_liste(self):
        self.assertEqual(await api.get_steps_for_many(ids=" , ", letzte=5, user=NUTZER, db=self.db), {"tasks": {}})


if __name__ == "__main__":
    unittest.main()
