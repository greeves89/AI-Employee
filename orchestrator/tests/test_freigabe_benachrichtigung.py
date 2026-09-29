"""Entschiedene Freigaben stehen nicht mehr mit Knoepfen in der Glocke.

29.09.2026: Zwei laengst freigegebene Anfragen zeigten in der Glocke weiter
„Jetzt senden / Abbrechen"; die Freigaben-Seite war leer. Der Test faehrt die
echte Abfrage (JSON-Feld approval_id) gegen eine echte Datenbank.
"""
import unittest
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.freigabe_benachrichtigung import benachrichtigungen_abschliessen
from app.models.notification import Notification


class AbschliessenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Notification.metadata.create_all, tables=[Notification.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add_all([
            Notification(agent_id="a1", type="approval", title="Frage 252", message="",
                         read=False, meta={"approval_id": 252, "options": ["Ja", "Nein"]}),
            Notification(agent_id="a1", type="approval", title="Frage 999", message="",
                         read=False, meta={"approval_id": 999}),
            Notification(agent_id="a1", type="info", title="Info", message="", read=False,
                         meta={"approval_id": 252}),
        ])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def zeile(self, titel):
        return (await self.db.execute(select(Notification).where(Notification.title == titel))).scalar_one()

    async def test_entschiedene_freigabe_wird_abgeschlossen(self):
        freigabe = SimpleNamespace(id=252, status=SimpleNamespace(value="approved"), user_response="Ja")
        self.assertEqual(await benachrichtigungen_abschliessen(self.db, [freigabe]), 1)
        await self.db.commit()
        n = await self.zeile("Frage 252")
        self.assertTrue(n.read)
        self.assertEqual(n.meta["entschieden"], "approved")
        self.assertEqual(n.meta["antwort"], "Ja")
        self.assertEqual(n.meta["options"], ["Ja", "Nein"])  # nichts anderes verloren

    async def test_andere_bleiben_unberuehrt(self):
        freigabe = SimpleNamespace(id=252, status=SimpleNamespace(value="denied"), user_response=None)
        await benachrichtigungen_abschliessen(self.db, [freigabe])
        await self.db.commit()
        self.assertFalse((await self.zeile("Frage 999")).read)
        self.assertFalse((await self.zeile("Info")).read)  # nur Freigabe-Meldungen

    async def test_leere_liste(self):
        self.assertEqual(await benachrichtigungen_abschliessen(self.db, []), 0)


if __name__ == "__main__":
    unittest.main()
