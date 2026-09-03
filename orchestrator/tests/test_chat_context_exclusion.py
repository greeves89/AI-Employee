"""Von Hand aus dem Kontext nehmen, ohne zu loeschen (#538 Punkt 4).

Der Unterschied zu ``compact_session`` ist die Koernung: dort wird ein ganzer
Abschnitt automatisch gefaltet, hier eine einzelne Nachricht von Hand — und
zwei Stufen davon, weil der Ballast meist aus der Werkzeug-Ausgabe kommt und
der Gespraechstext selten das Problem ist.
"""

import unittest
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.chat_history import (
    excluded_from_model,
    set_context_exclusion,
    tool_output_excluded,
)
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc


class ContextExclusionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (ChatMessage, ChatSession):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, db, *, with_tool_call=False):
        db.add(ChatMessage(
            agent_id="a1", session_id="s1", message_id="m1", role="assistant",
            content="Hier ist das Ergebnis.",
            tool_calls=({"name": "read_file"} if with_tool_call else None),
            timestamp=datetime.now(UTC), meta={},
        ))
        await db.commit()

    async def _reload(self, db, message_id="m1"):
        row = (await db.execute(
            select(ChatMessage).where(ChatMessage.message_id == message_id)
        )).scalar_one()
        return row

    async def test_unknown_scope_is_refused(self):
        async with self.Session() as db:
            await self._seed(db, with_tool_call=True)
            result = await set_context_exclusion(
                db, "a1", "s1", "m1", scope="anhang", excluded=True
            )
            self.assertFalse(result["ok"])

    async def test_unknown_message_is_refused(self):
        async with self.Session() as db:
            await self._seed(db, with_tool_call=True)
            result = await set_context_exclusion(
                db, "a1", "s1", "gibtsnicht", scope="message", excluded=True
            )
            self.assertFalse(result["ok"])

    async def test_tool_output_scope_needs_a_tool_call(self):
        """Sonst gaebe es nichts zu loesen — die Nachricht hat gar keine Ausgabe."""
        async with self.Session() as db:
            await self._seed(db, with_tool_call=False)
            result = await set_context_exclusion(
                db, "a1", "s1", "m1", scope="tool_output", excluded=True
            )
            self.assertFalse(result["ok"])

    async def test_whole_message_exclusion_marks_and_unmarks(self):
        async with self.Session() as db:
            await self._seed(db, with_tool_call=False)
            result = await set_context_exclusion(
                db, "a1", "s1", "m1", scope="message", excluded=True
            )
            await db.commit()
            self.assertTrue(result["ok"])
            row = await self._reload(db)
            self.assertTrue(excluded_from_model(row))

            await set_context_exclusion(db, "a1", "s1", "m1", scope="message", excluded=False)
            await db.commit()
            row = await self._reload(db)
            self.assertFalse(excluded_from_model(row))

    async def test_tool_output_exclusion_leaves_the_message_itself_live(self):
        """Nur die Werkzeug-Ausgabe ist geloest, der Gespraechstext bleibt im
        Kontext — sonst waere die feinere Koernung witzlos."""
        async with self.Session() as db:
            await self._seed(db, with_tool_call=True)
            await set_context_exclusion(db, "a1", "s1", "m1", scope="tool_output", excluded=True)
            await db.commit()

            row = await self._reload(db)
            self.assertTrue(tool_output_excluded(row))
            self.assertFalse(excluded_from_model(row))

    async def test_nothing_is_deleted(self):
        """Anders als ``rewind`` bleibt die Nachricht fuer den Menschen immer
        sichtbar — nur was ans Modell geht, aendert sich."""
        async with self.Session() as db:
            await self._seed(db, with_tool_call=True)
            await set_context_exclusion(db, "a1", "s1", "m1", scope="tool_output", excluded=True)
            await db.commit()

            rows = (await db.execute(select(ChatMessage))).scalars().all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].content, "Hier ist das Ergebnis.")

    async def test_another_session_is_untouched(self):
        async with self.Session() as db:
            await self._seed(db, with_tool_call=False)
            db.add(ChatMessage(
                agent_id="a1", session_id="s2", message_id="m1", role="assistant",
                content="Fremd", timestamp=datetime.now(UTC), meta={},
            ))
            await db.commit()

            await set_context_exclusion(db, "a1", "s1", "m1", scope="message", excluded=True)
            await db.commit()

            other = (await db.execute(
                select(ChatMessage).where(ChatMessage.session_id == "s2")
            )).scalars().all()
            self.assertFalse(excluded_from_model(other[0]))


class WiringTests(unittest.TestCase):
    def test_endpoint_exists(self):
        from app.api import agents

        paths = {r.path for r in agents.router.routes}
        self.assertIn(
            "/agents/{agent_id}/chat/sessions/{session_id}/messages/{message_id}/context",
            paths,
        )

    def test_endpoint_checks_ownership(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/agents.py").read_text()
        block = src.split("async def set_message_context_exclusion")[1].split("\n@router")[0]
        self.assertIn("_check_owner", block)

    def test_history_endpoint_distinguishes_agent_from_human(self):
        """Der Mensch sieht alles, der Agent selbst nur, was auch ans Modell
        geht — sonst waere Ausschliessen fuer die Modell-Sicht wirkungslos."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/agents.py").read_text()
        block = src.split("async def get_chat_history")[1].split("\n@router")[0]
        self.assertIn("is_agent_principal", block)
        self.assertIn("excluded_from_model", block)
        self.assertIn("tool_output_excluded", block)


if __name__ == "__main__":
    unittest.main()
