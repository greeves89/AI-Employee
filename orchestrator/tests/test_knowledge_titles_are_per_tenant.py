"""Titel im Wissensspeicher sind je Besitzer eindeutig, nicht global (Issue #655).

Die Tabelle trug seit Anlage eine globale Unique-Bedingung auf `title`, waehrend der
Schreibpfad laengst nur im Vault des Besitzers sucht. Folge: der zweite Mandant, der
einen generischen Titel wie "Klare Aufgabendefinition" erzeugt, laeuft in eine
UniqueViolation — und die reisst den kompletten Reflection-Lauf mit, nicht nur den
einen Eintrag.

Geprueft wird gegen ECHTES SQL (in-memory SQLite), nicht gegen einen Mock: die
Mandantentrennung passiert in der Query, ein Stub wuerde genau das wegtesten
(gleiches Muster wie test_activity_timeline.py).

Der AST-Test unten haelt die andere Haelfte fest: das Lockern der DB-Bedingung ist nur
dann sicher, wenn WIRKLICH JEDE Titelsuche auf den Besitzer eingeschraenkt ist. Eine
uneingeschraenkte Suche wuerde sonst ab jetzt entweder den Eintrag eines fremden
Mandanten ueberschreiben oder mit MultipleResultsFound abbrechen.
"""

import ast
import pathlib
import re
import unittest

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.knowledge import KnowledgeEntry

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "orchestrator" / "app"
MIGRATION = (
    REPO / "orchestrator" / "alembic" / "versions"
    / "b7c1e93a5f20_knowledge_title_unique_per_tenant.py"
)

TENANT_A = "user-a"
TENANT_B = "user-b"
SHARED_TITLE = "Klare Aufgabendefinition"


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # nur diese eine Tabelle — andere Modelle nutzen Postgres-Typen (JSONB),
        # die der SQLite-Dialekt nicht uebersetzen kann.
        await conn.run_sync(Base.metadata.create_all,
                            tables=[KnowledgeEntry.__table__])
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _entry(user_id, title=SHARED_TITLE, content="c"):
    return KnowledgeEntry(title=title, content=content, tags=[],
                          created_by="t", updated_by="t", user_id=user_id)


class TitleIsUniquePerTenantTests(unittest.IsolatedAsyncioTestCase):

    async def test_two_tenants_may_hold_the_same_title(self):
        """Der Fall aus dem Fehlerbericht: derselbe Titel bei zwei Besitzern."""
        engine, Session = await _session()
        try:
            async with Session() as db:
                db.add(_entry(TENANT_A))
                await db.commit()
            async with Session() as db:
                db.add(_entry(TENANT_B))
                await db.commit()   # vorher: UniqueViolation, Lauf komplett tot
            async with Session() as db:
                rows = (await db.execute(
                    select(KnowledgeEntry).where(KnowledgeEntry.title == SHARED_TITLE)
                )).scalars().all()
            self.assertEqual({r.user_id for r in rows}, {TENANT_A, TENANT_B})
        finally:
            await engine.dispose()

    async def test_owner_scoped_lookup_returns_only_the_own_entry(self):
        """Die Suche des Schreibpfads darf trotz Namensgleichheit nicht kreuzen."""
        engine, Session = await _session()
        try:
            async with Session() as db:
                db.add(_entry(TENANT_A, content="gehoert A"))
                db.add(_entry(TENANT_B, content="gehoert B"))
                await db.commit()
            async with Session() as db:
                found = (await db.execute(select(KnowledgeEntry).where(
                    KnowledgeEntry.title == SHARED_TITLE,
                    KnowledgeEntry.user_id == TENANT_B,
                ))).scalar_one()          # scalar_one: genau EINER, sonst Fehler
            self.assertEqual(found.content, "gehoert B")
        finally:
            await engine.dispose()

    async def test_write_entry_does_not_touch_the_other_tenant(self):
        """write_entry legt fuer B neu an, statt A zu ueberschreiben."""
        from app.core.knowledge_write import write_entry

        engine, Session = await _session()
        try:
            async with Session() as db:
                db.add(_entry(TENANT_A, content="gehoert A"))
                await db.commit()
            async with Session() as db:
                entry, created = await write_entry(
                    db, user_id=TENANT_B, title=SHARED_TITLE,
                    content="gehoert B", author="test",
                )
            self.assertTrue(created)
            self.assertEqual(entry.user_id, TENANT_B)
            async with Session() as db:
                a = (await db.execute(select(KnowledgeEntry).where(
                    KnowledgeEntry.user_id == TENANT_A
                ))).scalar_one()
            self.assertEqual(a.content, "gehoert A")
        finally:
            await engine.dispose()

    def test_model_declares_no_global_unique_on_title(self):
        """Waechter: unique=True am Feld wuerde die Bedingung wieder global machen."""
        col = sa_inspect(KnowledgeEntry).columns["title"]
        self.assertFalse(col.unique, "title darf nicht global eindeutig sein")
        self.assertTrue(col.index, "Suchen nach title bleiben indiziert")

    def test_migration_installs_both_partial_indexes(self):
        """NULLs gelten in Postgres als verschieden — ein Index auf (title, user_id)
        allein wuerde mehrfache GLOBALE Eintraege gleichen Titels erlauben."""
        # benachbarte String-Literale zusammenziehen, damit der Zeilenumbruch im
        # Quelltext den Vergleich nicht zerlegt
        sql = re.sub(r'"\s*\n\s*"', "", MIGRATION.read_text())
        self.assertIn("DROP INDEX IF EXISTS ix_knowledge_entries_title", sql)
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entries_title_global "
            "ON knowledge_entries (title) WHERE user_id IS NULL", sql)
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entries_title_per_user "
            "ON knowledge_entries (title, user_id) WHERE user_id IS NOT NULL", sql)
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_entries_title "
            "ON knowledge_entries (title)", sql)


class EveryTitleLookupIsOwnerScopedTests(unittest.TestCase):
    """Formtest: kein `KnowledgeEntry.title == ...` ohne `user_id` im selben Aufruf.

    Zur Laufzeit haette eine uebersehene Stelle keine Spur hinterlassen — sie wuerde
    still den Eintrag eines fremden Mandanten treffen. Deshalb am Quelltext pruefen.
    """

    def _unscoped_lookups(self):
        treffer = []
        for path in sorted(APP.rglob("*.py")):
            baum = ast.parse(path.read_text(), filename=str(path))
            for knoten in ast.walk(baum):
                if not isinstance(knoten, ast.Call):
                    continue
                quelle = ast.dump(knoten)
                gleichheit = (
                    "attr='title'" in quelle
                    and "Eq()" in quelle
                    and "id='KnowledgeEntry'" in quelle
                )
                if gleichheit and "attr='user_id'" not in quelle:
                    treffer.append(f"{path.relative_to(REPO)}:{knoten.lineno}")
        return treffer

    def test_no_unscoped_title_lookup_remains(self):
        offen = self._unscoped_lookups()
        self.assertEqual(offen, [], "Titelsuche ohne Besitzer-Einschraenkung: "
                                    + ", ".join(offen))


if __name__ == "__main__":
    unittest.main()
