"""Eine Chat-Zeile, zwei Schreiber — und zwei Wege, sie zu verlieren.

Beide Fehler kamen aus derselben Meldung: „er zeigt an, dass er fertig ist, aber
wenn ich reingehe, sehe ich nur die Tool-Calls" und „ich habe im anderen Chat
nach dem Status gefragt und bekomme etwas zu einer ganz anderen Sache".

**Verloren.** Beim Trennen der Verbindung schreibt der Browser einen
Zwischenstand weg: die früh gekommenen Werkzeugaufrufe ja, den am Ende
gekommenen Antworttext nein. Danach kam das serverseitige ``done`` mit dem
fertigen Text, fand die Zeile — und übersprang sie. Der Chat blieb für immer
ohne Antwort stehen.

**Vertauscht.** Die Antwortzeile wurde notfalls unter der Sitzung gespeichert,
die in dieser Verbindung *gerade offen* war. Nach einem Verbindungsabbruch ist
die Zuordnung Nachricht→Sitzung leer, und die Antwort landete im falschen
Gespräch.

Gegen echtes SQL, weil beides an Abfragen hängt: „gibt es die Zeile schon" und
„zu welcher Sitzung gehört diese Nachricht".
"""

import os
import tempfile
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.chat_message import ChatMessage

AGENT = "020ea0d1"
SESSION_A = "sess-systemlandkarte"
SESSION_B = "sess-kodierung"


class ChatPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Echte Datei statt ":memory:": ":memory:" ist pro Verbindung eine
        # EIGENE Datenbank (SQLite-Eigenheit) — zwei "gleichzeitige" Schreiber
        # (test_a_true_write_race_does_not_drop_the_loser) braeuchten dafuer
        # dieselbe physische Verbindung, und die teilt sich in SQLAlchemy nicht
        # nebenlaeufig: eine zweite Transaktion auf derselben Verbindung stoert
        # die erste (Rollback der einen reisst die andere mit). Eine Datei
        # dagegen bekommt aus dem Pool ganz normal getrennte Verbindungen, so
        # wie Postgres auch — genau das macht die Nebenlaeufigkeit im Race-Test
        # erst echt.
        fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self._db_path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatMessage.metadata.create_all,
                                tables=[ChatMessage.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        import app.services.chat_persistence as cp
        self._cp = cp
        self._orig = cp.async_session_factory
        cp.async_session_factory = self.Session

    async def asyncTearDown(self):
        self._cp.async_session_factory = self._orig
        await self.engine.dispose()
        os.unlink(self._db_path)

    async def _rows(self, **where):
        from sqlalchemy import select
        async with self.Session() as db:
            q = select(ChatMessage)
            for k, v in where.items():
                q = q.where(getattr(ChatMessage, k) == v)
            return (await db.execute(q.order_by(ChatMessage.id))).scalars().all()

    # ── Der verlorene Text ────────────────────────────────────────────────

    async def test_a_partial_row_gets_its_text_later(self):
        """Der eigentliche Fehler: erst Werkzeugaufrufe ohne Text, dann das
        fertige ``done`` — und der Text muss ankommen."""
        tools = [{"tool": "read_file", "input": "{}"}]
        await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="", tool_calls=tools,
            meta={"partial": True},
        )
        await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="Status: MVP fertig.",
        )
        rows = await self._rows(message_id="m1")
        self.assertEqual(len(rows), 1, "Ergaenzen, nicht verdoppeln")
        self.assertEqual(rows[0].content, "Status: MVP fertig.")
        self.assertEqual(rows[0].tool_calls, tools, "Werkzeugaufrufe bleiben")

    async def test_an_empty_update_never_erases_an_answer(self):
        """Die Reihenfolge ist nicht garantiert. Kommt der Zwischenstand ZULETZT,
        darf er die fertige Antwort nicht auslöschen."""
        await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="Die fertige Antwort.")
        await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="", tool_calls=[{"tool": "x", "input": "{}"}])
        rows = await self._rows(message_id="m1")
        self.assertEqual(rows[0].content, "Die fertige Antwort.")

    async def test_it_reports_whether_the_user_has_seen_this(self):
        """Rückgabe steuert die Benachrichtigung."""
        first = await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="", tool_calls=[{"tool": "x", "input": "{}"}])
        self.assertTrue(first, "Neue Zeile — der Nutzer weiss noch nichts davon")

        filled = await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="Endlich der Text.")
        self.assertTrue(filled, "War leer, bekommt Text — der Nutzer war weg")

        again = await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="Endlich der Text.")
        self.assertFalse(again, "Nichts Neues — nicht noch einmal melden")

    async def test_meta_and_cost_are_merged_not_replaced(self):
        await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="x", meta={"partial": True})
        await self._cp.upsert_chat_message(
            AGENT, SESSION_A, "m1", "assistant", content="x",
            meta={"num_turns": 3}, cost_usd=0.42)
        rows = await self._rows(message_id="m1")
        self.assertEqual(rows[0].meta.get("partial"), True)
        self.assertEqual(rows[0].meta.get("num_turns"), 3)
        self.assertAlmostEqual(rows[0].cost_usd, 0.42)

    async def test_a_true_write_race_does_not_drop_the_loser(self):
        """Zwei Schreiber legen dieselbe NEUE Zeile echt gleichzeitig an (nicht
        nacheinander). Vor dem Fix verletzte der zweite Commit den Unique-Index,
        das wurde nur geloggt — der komplette Inhalt dieses Aufrufs (Text ODER
        Werkzeugaufrufe, je nachdem wer verlor) ging kommentarlos verloren."""
        import asyncio

        await asyncio.gather(
            self._cp.upsert_chat_message(
                AGENT, SESSION_A, "race", "assistant",
                content="Die fertige Antwort.",
            ),
            self._cp.upsert_chat_message(
                AGENT, SESSION_A, "race", "assistant",
                content="", tool_calls=[{"tool": "read_file", "input": "{}"}],
            ),
        )
        rows = await self._rows(message_id="race")
        self.assertEqual(len(rows), 1, "Unique-Index erzwingt genau eine Zeile")
        self.assertEqual(rows[0].content, "Die fertige Antwort.",
                          "Der Text darf nicht verloren gehen")
        self.assertEqual(rows[0].tool_calls, [{"tool": "read_file", "input": "{}"}],
                          "Die Werkzeugaufrufe des zweiten Schreibers duerfen nicht "
                          "stillschweigend verworfen werden")

    async def test_nothing_is_written_without_a_session(self):
        created = await self._cp.upsert_chat_message(AGENT, "", "m1", "assistant", content="x")
        self.assertFalse(created)
        self.assertEqual(await self._rows(), [])

    # ── Die vertauschte Unterhaltung ──────────────────────────────────────

    async def test_the_session_comes_from_the_user_row(self):
        """Die Nutzerzeile steht beim Absenden schon da und trägt die Wahrheit."""
        async with self.Session() as db:
            db.add(ChatMessage(agent_id=AGENT, session_id=SESSION_A,
                               message_id="m1", role="user", content="Status?"))
            await db.commit()
        got = await self._cp.session_for_message(AGENT, "m1")
        self.assertEqual(got, SESSION_A)

    async def test_a_foreign_message_has_no_session(self):
        """Sprache, Telegram, Hintergrundaufgabe: keine Nutzerzeile, also nichts
        zu raten. Der Rückfall auf „die gerade offene Unterhaltung" war genau
        der Grund, wieso eine Antwort im falschen Chat landete."""
        self.assertIsNone(await self._cp.session_for_message(AGENT, "unbekannt"))

    async def test_two_conversations_do_not_mix(self):
        async with self.Session() as db:
            db.add(ChatMessage(agent_id=AGENT, session_id=SESSION_A,
                               message_id="m1", role="user", content="Systemlandkarte?"))
            db.add(ChatMessage(agent_id=AGENT, session_id=SESSION_B,
                               message_id="m2", role="user", content="Kodierung?"))
            await db.commit()
        self.assertEqual(await self._cp.session_for_message(AGENT, "m1"), SESSION_A)
        self.assertEqual(await self._cp.session_for_message(AGENT, "m2"), SESSION_B)

    async def test_the_same_message_id_in_two_sessions_stays_apart(self):
        """Der Schlüssel enthält die Sitzung — sonst würde eine Antwort die
        andere überschreiben."""
        await self._cp.upsert_chat_message(AGENT, SESSION_A, "m1", "assistant", content="A")
        await self._cp.upsert_chat_message(AGENT, SESSION_B, "m1", "assistant", content="B")
        self.assertEqual([r.content for r in await self._rows(message_id="m1")], ["A", "B"])

    async def test_another_agent_is_another_world(self):
        async with self.Session() as db:
            db.add(ChatMessage(agent_id="fremder", session_id="s-fremd",
                               message_id="m1", role="user", content="?"))
            await db.commit()
        self.assertIsNone(await self._cp.session_for_message(AGENT, "m1"))


class ListenerContractTests(unittest.TestCase):
    """Der Lauscher darf eine vorhandene Zeile nicht mehr überspringen.

    Quelltext-Test, weil der Lauscher eine Endlosschleife auf Redis ist: hier
    zählt, dass die eine Zeile, die den Fehler ausmachte, nicht zurückkommt.
    """

    def test_the_listener_completes_instead_of_skipping(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
        block = src.split("async def _listen_chat_completions")[1].split("\nasync def ")[0]
        self.assertIn("upsert_chat_message", block,
                      "Der Lauscher muss ueber die gemeinsame Zusammenfuehrung gehen")
        self.assertNotIn("continue  # Already saved by WebSocket handler", block,
                         "Ueberspringen liess den Zwischenstand fuer immer ohne Text stehen")


if __name__ == "__main__":
    unittest.main()
