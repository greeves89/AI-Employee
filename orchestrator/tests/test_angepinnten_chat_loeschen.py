"""Ein angepinnter Chat ist geschützt — aber gezielt löschbar.

Bis 1.356.13 lehnte der Server das Löschen eines angepinnten Chats mit 409 ab, und
die Oberfläche schluckte den Fehler: Das X tat scheinbar nichts (03.10.2026). Der
Schutz bleibt (etwa vor „Alle Chats löschen“); wer EINEN angepinnten Chat nach
Rückfrage löscht, schickt ``force=true``.

MC/DC über (angepinnt, force).
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import agents as agents_api
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession

NUTZER = SimpleNamespace(id="u1")


class AngepinntLoeschenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatSession.metadata.create_all,
                                tables=[ChatSession.__table__, ChatMessage.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _anlegen(self, pinned: bool):
        async with self.Session() as db:
            db.add(ChatSession(agent_id="a1", session_id="s1", pinned=pinned))
            await db.commit()

    async def _loeschen(self, force: bool):
        with patch.object(agents_api, "_check_owner", AsyncMock()):
            async with self.Session() as db:
                return await agents_api.delete_chat_session("a1", "s1", force=force, user=NUTZER, db=db)

    async def _noch_da(self) -> bool:
        async with self.Session() as db:
            return (await db.execute(select(ChatSession))).scalar_one_or_none() is not None

    async def test_angepinnt_ohne_force_bleibt_geschuetzt(self):
        await self._anlegen(pinned=True)
        with self.assertRaises(HTTPException) as fehler:
            await self._loeschen(force=False)
        self.assertEqual(fehler.exception.status_code, 409)
        self.assertTrue(await self._noch_da())

    async def test_angepinnt_mit_force_wird_geloescht(self):
        await self._anlegen(pinned=True)
        await self._loeschen(force=True)
        self.assertFalse(await self._noch_da())

    async def test_nicht_angepinnt_wird_ohne_force_geloescht(self):
        await self._anlegen(pinned=False)
        await self._loeschen(force=False)
        self.assertFalse(await self._noch_da())


if __name__ == "__main__":
    unittest.main()
