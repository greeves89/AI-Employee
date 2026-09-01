"""Gespeicherte Meetings: userbased wie ueberall sonst in der App.

Vorher gab es keine Persistenz ueberhaupt (Transkript nur im Arbeitsspeicher
des Clients) — diese Tests sichern die neue CRUD-Flaeche direkt gegen
Fremdzugriff ab, wie bei jedem neuen Endpunkt Pflicht (siehe
test_eval_isolation.py fuer dasselbe Muster).
"""

import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.meetings import create_meeting, delete_meeting, get_meeting, list_meetings, update_meeting
from app.models.meeting import Meeting
from app.schemas.meeting import MeetingCreate, MeetingUpdate


def _user(uid: str):
    return SimpleNamespace(id=uid, role="user", email=f"{uid}@example.test")


class MeetingCrudTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Meeting.metadata.create_all, tables=[Meeting.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_created_meeting_is_persisted_and_owned(self):
        async with self.Session() as db:
            created = await create_meeting(
                MeetingCreate(title="Standup", transcript="Wir sind im Plan.",
                              participants=["Anna", "Ben"], duration_seconds=120),
                user=_user("anna"), db=db,
            )
        self.assertEqual(created.title, "Standup")
        self.assertEqual(created.participants, ["Anna", "Ben"])

        async with self.Session() as db:
            fetched = await get_meeting(created.id, user=_user("anna"), db=db)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.transcript, "Wir sind im Plan.")

    async def test_list_is_scoped_to_the_calling_user(self):
        async with self.Session() as db:
            await create_meeting(MeetingCreate(title="Annas Meeting"), user=_user("anna"), db=db)
        async with self.Session() as db:
            await create_meeting(MeetingCreate(title="Bobs Meeting"), user=_user("bob"), db=db)

        async with self.Session() as db:
            anna_list = await list_meetings(user=_user("anna"), db=db)
        titles = {m.title for m in anna_list.meetings}
        self.assertIn("Annas Meeting", titles)
        self.assertNotIn("Bobs Meeting", titles)
        self.assertEqual(anna_list.total, 1)

    async def test_reading_a_foreign_meeting_is_refused(self):
        async with self.Session() as db:
            created = await create_meeting(MeetingCreate(title="Privat"), user=_user("anna"), db=db)

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await get_meeting(created.id, user=_user("bob"), db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_a_missing_meeting_is_404_not_403(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await get_meeting("does-not-exist", user=_user("anna"), db=db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_rename_and_edit_participants(self):
        async with self.Session() as db:
            created = await create_meeting(
                MeetingCreate(title="Alt", participants=["Anna"]), user=_user("anna"), db=db,
            )
        async with self.Session() as db:
            updated = await update_meeting(
                created.id, MeetingUpdate(title="Neu", participants=["Anna", "Ben"]),
                user=_user("anna"), db=db,
            )
        self.assertEqual(updated.title, "Neu")
        self.assertEqual(updated.participants, ["Anna", "Ben"])

    async def test_updating_a_foreign_meeting_is_refused(self):
        async with self.Session() as db:
            created = await create_meeting(MeetingCreate(title="Privat"), user=_user("anna"), db=db)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await update_meeting(created.id, MeetingUpdate(title="Uebernommen"),
                                      user=_user("bob"), db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_deleting_a_foreign_meeting_is_refused_and_leaves_it_intact(self):
        async with self.Session() as db:
            created = await create_meeting(MeetingCreate(title="Privat"), user=_user("anna"), db=db)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await delete_meeting(created.id, user=_user("bob"), db=db)
        self.assertEqual(ctx.exception.status_code, 403)
        async with self.Session() as db:
            still_there = await get_meeting(created.id, user=_user("anna"), db=db)
        self.assertEqual(still_there.id, created.id)

    async def test_owner_can_delete_their_own_meeting(self):
        async with self.Session() as db:
            created = await create_meeting(MeetingCreate(title="Privat"), user=_user("anna"), db=db)
        async with self.Session() as db:
            await delete_meeting(created.id, user=_user("anna"), db=db)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await get_meeting(created.id, user=_user("anna"), db=db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_blank_title_is_rejected(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            MeetingCreate(title="   ")


if __name__ == "__main__":
    unittest.main()
