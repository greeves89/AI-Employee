"""Verdichten im selben Gespräch (``/compact``).

Der Unterschied zu „zusammenfassen" ist der, den ein Nutzer meint, wenn er
„compact" sagt: er will **hier** weiterreden, nur mit weniger Ballast. Ein
frisches Gespräch wäre eine Antwort auf eine andere Frage.

Und: es funktioniert in **allen drei Laufzeiten** gleich, weil es auf dem hier
gespeicherten Verlauf arbeitet und nicht im Agenten. Die Kompaktierung innerhalb
von Claude Code oder Codex lässt sich von aussen nicht anstossen.
"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.chat_history import (
    COMPACT_MARKER,
    KEEP_VERBATIM,
    compact_session,
)
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc


class CompactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (ChatMessage, ChatSession):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, db, count: int):
        base = datetime.now(UTC) - timedelta(hours=count)
        for i in range(count):
            db.add(ChatMessage(
                agent_id="a1", session_id="s1", message_id=f"m{i}",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Nachricht {i} mit etwas Inhalt",
                timestamp=base + timedelta(minutes=i), meta={},
            ))
        await db.commit()

    async def _live(self, db):
        rows = (await db.execute(select(ChatMessage).order_by(ChatMessage.timestamp))).scalars().all()
        return [m for m in rows if not (m.meta or {}).get("compacted")]

    async def test_old_messages_are_marked_not_deleted(self):
        """Verdichten heisst nicht verlieren — fuer den Menschen bleibt der
        Verlauf lesbar."""
        async with self.Session() as db:
            await self._seed(db, 20)
            result = await compact_session(db, "a1", "s1")
            await db.commit()

            self.assertTrue(result["ok"])
            all_rows = (await db.execute(select(ChatMessage))).scalars().all()
            # 20 Originale + 1 Zusammenfassung, nichts geloescht.
            self.assertEqual(len(all_rows), 21)
            folded = [m for m in all_rows if (m.meta or {}).get("compacted")]
            self.assertEqual(len(folded), 20 - KEEP_VERBATIM)

    async def test_the_newest_messages_stay_verbatim(self):
        """Die juengste Werkzeug-Ein- und -Ausgabe ist zusammengefasst wertlos."""
        async with self.Session() as db:
            await self._seed(db, 20)
            await compact_session(db, "a1", "s1")
            await db.commit()

            live = await self._live(db)
            texts = [m.content for m in live]
            self.assertTrue(any(t.startswith(COMPACT_MARKER) for t in texts))
            self.assertIn("Nachricht 19 mit etwas Inhalt", texts)
            self.assertNotIn("Nachricht 0 mit etwas Inhalt", texts)

    async def test_the_summary_sits_before_what_it_summarises(self):
        """Sonst staende die Zusammenfassung NACH dem, was sie zusammenfasst."""
        async with self.Session() as db:
            await self._seed(db, 20)
            await compact_session(db, "a1", "s1")
            await db.commit()

            live = await self._live(db)
            self.assertTrue(live[0].content.startswith(COMPACT_MARKER))

    async def test_a_short_conversation_is_refused(self):
        async with self.Session() as db:
            await self._seed(db, 4)
            result = await compact_session(db, "a1", "s1")
            self.assertFalse(result["ok"])
            self.assertIn("kurz", result["reason"].lower())

    async def test_compacting_twice_only_folds_what_is_new(self):
        """Sonst wuerde die Zusammenfassung bei jedem Mal erneut zusammengefasst."""
        async with self.Session() as db:
            await self._seed(db, 20)
            await compact_session(db, "a1", "s1")
            await db.commit()
            first = len(await self._live(db))

            # Neue Nachrichten dazu, dann nochmal.
            base = datetime.now(UTC)
            for i in range(20, 32):
                db.add(ChatMessage(
                    agent_id="a1", session_id="s1", message_id=f"m{i}", role="user",
                    content=f"Nachricht {i}", timestamp=base + timedelta(minutes=i), meta={},
                ))
            await db.commit()

            result = await compact_session(db, "a1", "s1")
            await db.commit()
            self.assertTrue(result["ok"])
            live = await self._live(db)
            self.assertEqual(len(live), KEEP_VERBATIM + 1)
            self.assertLessEqual(len(live), first + 12)

    async def test_nothing_to_fold_is_refused_cleanly(self):
        """Genau KEEP_VERBATIM Nachrichten: es gaebe nichts zu falten."""
        async with self.Session() as db:
            await self._seed(db, KEEP_VERBATIM)
            result = await compact_session(db, "a1", "s1")
            self.assertFalse(result["ok"])

    async def test_another_session_is_untouched(self):
        async with self.Session() as db:
            await self._seed(db, 20)
            db.add(ChatMessage(
                agent_id="a1", session_id="s2", message_id="x1", role="user",
                content="Fremd", timestamp=datetime.now(UTC), meta={},
            ))
            await db.commit()

            await compact_session(db, "a1", "s1")
            await db.commit()

            other = (await db.execute(
                select(ChatMessage).where(ChatMessage.session_id == "s2")
            )).scalars().all()
            self.assertEqual(len(other), 1)
            self.assertFalse((other[0].meta or {}).get("compacted"))


if __name__ == "__main__":
    unittest.main()
