"""Die vault_chunks-Reparatur gegen ein ECHTES PostgreSQL (#834).

Alle anderen Tests zu dieser Reparatur pruefen Zeichenketten oder fahren gegen
eine Attrappe, die jedes SQL kommentarlos schluckt. Ein Tippfehler in
``vector_cosine_ops``, eine ungueltige generierte Spalte oder eine Anweisung,
die beim ZWEITEN Start umfaellt, kaeme dort gruen durch -- und schluege erst
beim Betreiber zu, wo sie wieder nur als Warnung im Log landet.

Dieser Test baut deshalb genau den gemeldeten Zustand nach -- Tabelle aus dem
ORM-Modell, also ohne ``embedding`` und ohne ``ts`` -- und belegt dreierlei:
  1. Die Anweisungen sind syntaktisch gueltig und stellen das Schema her.
  2. Sie sind wiederholbar (zweiter Lauf, wie beim naechsten Neustart).
  3. Der Einfuegeweg des Indexers, der vorher scheiterte, traegt danach --
     mit und ohne Vektor, ohne ``updated_at`` zu setzen.

Ohne erreichbares PostgreSQL wird uebersprungen; die CI stellt
``pgvector/pgvector:pg16`` bereit.
"""

import asyncio
import os
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.vault_chunks_schema import ensure_vault_chunks_schema
from tests.test_migrationen_gegen_echtes_postgres import _DB_URL, _postgres_da


@unittest.skipUnless(
    _postgres_da(),
    "Kein erreichbares PostgreSQL (DATABASE_URL) — in der CI laeuft dieser Test.",
)
class ReparaturStelltDasSchemaWirklichHer(unittest.TestCase):

    def setUp(self):
        self.basis = _DB_URL.rsplit("/", 1)[0]
        self.name = "vaulttest_" + uuid.uuid4().hex[:10]
        self.ziel = f"{self.basis}/{self.name}"
        asyncio.run(self._admin(f'CREATE DATABASE "{self.name}"'))

    def tearDown(self):
        asyncio.run(self._admin(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :n AND pid <> pg_backend_pid()", n=self.name))
        asyncio.run(self._admin(f'DROP DATABASE IF EXISTS "{self.name}"'))

    async def _admin(self, sql, **p):
        e = create_async_engine(f"{self.basis}/postgres", isolation_level="AUTOCOMMIT")
        try:
            async with e.connect() as c:
                r = await c.execute(text(sql), p)
                return r.fetchall() if r.returns_rows else None
        finally:
            await e.dispose()

    async def _lauf(self):
        """Kaputten Zustand bauen, zweimal reparieren, Ergebnis messen."""
        os.environ["DATABASE_URL"] = self.ziel
        from app.models import Base

        engine = create_async_engine(self.ziel)
        try:
            # Der gemeldete Zustand: Tabelle allein aus dem Modell.
            async with engine.begin() as c:
                await c.run_sync(
                    Base.metadata.create_all,
                    tables=[Base.metadata.tables["vault_chunks"]],
                )
            vorher = await self._spalten(engine)
            assert "embedding" not in vorher and "ts" not in vorher, (
                "Vorbedingung verfehlt: create_all hat die Spalten angelegt, "
                f"der Testaufbau zeigt den Fehler gar nicht ({sorted(vorher)})"
            )

            erster = await ensure_vault_chunks_schema(engine)
            zweiter = await ensure_vault_chunks_schema(engine)  # wie der naechste Start
            nachher = await self._spalten(engine)

            async with engine.begin() as c:
                indizes = {r[0] for r in (await c.execute(text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'vault_chunks'"
                ))).fetchall()}
                # Genau die beiden Einfuegewege des Indexers -- ohne updated_at.
                await c.execute(text(
                    "INSERT INTO vault_chunks (brain_label, path, chunk_idx, content, file_hash) "
                    "VALUES ('b', 'p.md', 0, 'Ein Satz ueber Recht.', 'h')"))
                await c.execute(text(
                    "INSERT INTO vault_chunks (brain_label, path, chunk_idx, content, file_hash, embedding) "
                    "VALUES ('b', 'p.md', 1, 'Noch einer.', 'h', CAST(:v AS vector))"),
                    {"v": str([0.1] * 1024)})
                zeilen = (await c.execute(text(
                    "SELECT count(*), count(updated_at), count(ts) FROM vault_chunks"
                ))).first()
            return erster, zweiter, nachher, indizes, zeilen
        finally:
            await engine.dispose()

    async def _spalten(self, engine) -> set[str]:
        async with engine.begin() as c:
            return {r[0] for r in (await c.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'vault_chunks'"))).fetchall()}

    def test_reparatur_ist_gueltig_wiederholbar_und_macht_einfuegen_moeglich(self):
        erster, zweiter, spalten, indizes, zeilen = asyncio.run(self._lauf())

        self.assertEqual(erster, [], "erste Reparatur: Anweisungen gescheitert")
        self.assertEqual(zweiter, [], "zweite Reparatur (Neustart): nicht wiederholbar")

        self.assertIn("embedding", spalten)
        self.assertIn("ts", spalten)
        for index in ("ix_vault_chunks_brain_path", "ix_vault_chunks_ts",
                      "ix_vault_chunks_embedding"):
            self.assertIn(index, indizes)

        anzahl, mit_zeit, mit_ts = zeilen
        self.assertEqual(anzahl, 2, "beide Einfuegewege muessen tragen")
        self.assertEqual(mit_zeit, 2, "updated_at ohne Vorgabewert -> NOT NULL-Verletzung")
        self.assertEqual(mit_ts, 2, "ts wird nicht erzeugt")


if __name__ == "__main__":
    unittest.main()
