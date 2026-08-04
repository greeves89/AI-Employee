"""#477: „Womit hängt dieser Punkt zusammen?" muss das beantworten, was der Graph ZEICHNET.

`/brain/related` lieferte bisher nur semantische Nachbarn (Kosinus-Ähnlichkeit aus
`brain_links`). Der Wissensgraph zeichnet aber ZWEI Kantenarten — `backlink` aus
`[[wikilinks]]` und `semantic`. Auf die Frage nach den Verbindungen bekam der Nutzer
also andere Knoten genannt, als er vor sich sah.

`_wikilink_neighbours` schließt die Lücke und benutzt exakt dieselbe Regel wie
`/brain/graph` beim Zeichnen: Titel-Treffer innerhalb der sichtbaren Einträge.
"""

import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.brain import _wikilink_neighbours
from app.models.knowledge import KnowledgeEntry

ME = "user-1"
OTHER = "user-2"


class WikilinkNeighbourTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                KnowledgeEntry.metadata.create_all, tables=[KnowledgeEntry.__table__]
            )
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def add(self, title, content, user_id=ME):
        e = KnowledgeEntry(
            title=title, content=content, tags=[], user_id=user_id,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        self.db.add(e)
        await self.db.commit()
        await self.db.refresh(e)
        return e

    async def test_outgoing_link_is_found(self):
        a = await self.add("Projekt Alpha", "siehe [[Budget 2026]] für Details")
        await self.add("Budget 2026", "Zahlen")
        n = await _wikilink_neighbours(a, ME, 10, self.db)
        self.assertEqual([(x["title"], x["direction"]) for x in n], [("Budget 2026", "outgoing")])

    async def test_incoming_link_is_found(self):
        await self.add("Projekt Alpha", "siehe [[Budget 2026]]")
        b = await self.add("Budget 2026", "Zahlen")
        n = await _wikilink_neighbours(b, ME, 10, self.db)
        self.assertEqual([(x["title"], x["direction"]) for x in n], [("Projekt Alpha", "incoming")])

    async def test_mutual_link_appears_once_as_both(self):
        """Sonst nennt der Agent denselben Nachbarn zweimal."""
        a = await self.add("Projekt Alpha", "siehe [[Budget 2026]]")
        await self.add("Budget 2026", "gehört zu [[Projekt Alpha]]")
        n = await _wikilink_neighbours(a, ME, 10, self.db)
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]["direction"], "both")

    async def test_link_to_a_nonexistent_note_is_ignored(self):
        """Ein [[Platzhalter]] ohne Notiz dahinter ist keine Kante — der Graph
        zeichnet ihn auch nicht."""
        a = await self.add("Projekt Alpha", "siehe [[Gibt Es Nicht]]")
        self.assertEqual(await _wikilink_neighbours(a, ME, 10, self.db), [])

    async def test_self_reference_is_not_a_neighbour(self):
        a = await self.add("Projekt Alpha", "vgl. [[Projekt Alpha]] weiter oben")
        self.assertEqual(await _wikilink_neighbours(a, ME, 10, self.db), [])

    async def test_notes_of_another_user_stay_invisible(self):
        """Mandantentrennung: ein fremder Eintrag darf weder als Ziel noch als
        Quelle einer Verbindung auftauchen."""
        a = await self.add("Projekt Alpha", "siehe [[Fremde Notiz]]")
        await self.add("Fremde Notiz", "verweist auf [[Projekt Alpha]]", user_id=OTHER)
        self.assertEqual(await _wikilink_neighbours(a, ME, 10, self.db), [])

    async def test_admin_sees_across_users(self):
        """`only_user_id=None` = Admin — wie im Graphen selbst."""
        a = await self.add("Projekt Alpha", "siehe [[Fremde Notiz]]")
        await self.add("Fremde Notiz", "x", user_id=OTHER)
        n = await _wikilink_neighbours(a, None, 10, self.db)
        self.assertEqual([x["title"] for x in n], ["Fremde Notiz"])

    async def test_limit_is_honoured(self):
        a = await self.add("Hub", "[[A]] [[B]] [[C]] [[D]]")
        for t in ("A", "B", "C", "D"):
            await self.add(t, "…")
        self.assertEqual(len(await _wikilink_neighbours(a, ME, 2, self.db)), 2)

    async def test_empty_content_does_not_crash(self):
        a = await self.add("Leer", "")
        self.assertEqual(await _wikilink_neighbours(a, ME, 10, self.db), [])


if __name__ == "__main__":
    unittest.main()
