"""#865: Die DLP-Standardregeln werden auch dann angelegt, wenn dlp_rules aus create_all stammt.

Der gemeldete Zustand (Raspberry-Pi-Anlage, frisch aufgesetzt): bei JEDEM Start

    Could not ensure dlp_rules table: ... NotNullViolationError:
    null value in column "created_at" of relation "dlp_rules"

und eine leere Regeltabelle. Die Tabelle war aus dem Modell entstanden
(``create_all``), das ``created_at``/``enabled`` nur mit Python-Standardwerten
kannte — NOT NULL ohne Datenbank-Standard. Der rohe Seed-INSERT liess
``created_at`` weg.

Geprueft wird:
  1. Modell, Anweisungen und Startpfad passen zusammen (ohne Datenbank).
  2. Das Seed-Verhalten mit einer Attrappe: nur bei leerer Tabelle, genau einmal.
  3. Gegen echtes PostgreSQL: der Zustand einer betroffenen Anlage (Tabelle wie
     vom ALTEN Modell per create_all angelegt) wird repariert und geseedet —
     und eine Neuanlage aus dem neuen Modell ebenso. Ohne erreichbares
     PostgreSQL wird dieser Teil uebersprungen; in der CI laeuft er.
"""

import asyncio
import os
import unittest
import uuid
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, String, Table, text,
)
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.dlp import DEFAULT_ACTIONS
from app.core.dlp_rules_schema import (
    ENSURE_STATEMENTS,
    SEED_STATEMENT,
    ensure_dlp_rules_schema,
)
from app.models.dlp_rule import DlpRule

_MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
_DB_URL = os.environ.get("DATABASE_URL", "")


class ModellUndStartpfadTests(unittest.TestCase):
    def test_model_has_database_defaults(self):
        """Sonst legt create_all die Spalten wieder ohne Standard an."""
        cols = DlpRule.__table__.c
        self.assertIsNotNone(cols.created_at.server_default)
        self.assertIsNotNone(cols.enabled.server_default)

    def test_seed_insert_sets_created_at_itself(self):
        self.assertIn("created_at", SEED_STATEMENT)

    def test_existing_tables_get_the_defaults(self):
        joined = " ".join(ENSURE_STATEMENTS)
        self.assertIn("ALTER COLUMN created_at SET DEFAULT now()", joined)
        self.assertIn("ALTER COLUMN enabled SET DEFAULT true", joined)

    def test_startup_uses_the_helper_and_the_old_insert_is_gone(self):
        src = _MAIN.read_text()
        self.assertIn("ensure_dlp_rules_schema(", src)
        self.assertNotIn("INSERT INTO dlp_rules (pii_class, agent_id, action, enabled) ", src)


class _Ergebnis:
    def __init__(self, wert):
        self._wert = wert

    def scalar(self):
        return self._wert


class _Verbindung:
    def __init__(self, protokoll, vorhandene_globale):
        self._protokoll = protokoll
        self._vorhandene = vorhandene_globale

    async def execute(self, anweisung, params=None):
        self._protokoll.append((str(anweisung), params))
        if "SELECT count(*)" in str(anweisung):
            return _Ergebnis(self._vorhandene)
        return _Ergebnis(None)


class _Transaktion:
    def __init__(self, verbindung):
        self._verbindung = verbindung

    async def __aenter__(self):
        return self._verbindung

    async def __aexit__(self, *exc):
        return False


class _Engine:
    def __init__(self, vorhandene_globale):
        self.protokoll = []
        self._verbindung = _Verbindung(self.protokoll, vorhandene_globale)

    def begin(self):
        return _Transaktion(self._verbindung)


class SeedVerhaltenTests(unittest.TestCase):
    def test_empty_table_is_seeded_with_every_default(self):
        engine = _Engine(vorhandene_globale=0)
        self.assertEqual(asyncio.run(ensure_dlp_rules_schema(engine)), len(DEFAULT_ACTIONS))
        inserts = [p for s, p in engine.protokoll if s.startswith("INSERT")]
        self.assertEqual({p["c"]: p["a"] for p in inserts}, DEFAULT_ACTIONS)

    def test_existing_global_rules_are_left_alone(self):
        """Was ein Admin geaendert oder geloescht hat, bleibt so."""
        engine = _Engine(vorhandene_globale=3)
        self.assertEqual(asyncio.run(ensure_dlp_rules_schema(engine)), 0)
        self.assertFalse([s for s, _ in engine.protokoll if s.startswith("INSERT")])

    def test_repair_runs_before_the_seed(self):
        engine = _Engine(vorhandene_globale=0)
        asyncio.run(ensure_dlp_rules_schema(engine))
        texte = [s for s, _ in engine.protokoll]
        erster_insert = next(i for i, s in enumerate(texte) if s.startswith("INSERT"))
        for alter in (i for i, s in enumerate(texte) if s.startswith("ALTER")):
            self.assertLess(alter, erster_insert)


def _postgres_da() -> bool:
    if "postgresql" not in _DB_URL:
        return False

    async def _versuch():
        e = create_async_engine(_DB_URL.rsplit("/", 1)[0] + "/postgres",
                                isolation_level="AUTOCOMMIT")
        try:
            async with e.connect():
                return True
        finally:
            await e.dispose()

    try:
        return asyncio.run(_versuch())
    except Exception:
        return False


def _tabelle_wie_altes_modell(metadata: MetaData) -> Table:
    """dlp_rules so, wie create_all sie aus dem Modell VOR #865 angelegt hat:
    NOT NULL ohne Datenbank-Standard, ORM-Indexnamen."""
    return Table(
        "dlp_rules", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pii_class", String(40), nullable=False, index=True),
        Column("agent_id", String, nullable=True, index=True),
        Column("action", String(20), nullable=False),
        Column("enabled", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )


@unittest.skipUnless(
    _postgres_da(),
    "Kein erreichbares PostgreSQL (DATABASE_URL) — in der CI laeuft dieser Test.",
)
class GegenEchtesPostgres(unittest.TestCase):

    def setUp(self):
        self.basis = _DB_URL.rsplit("/", 1)[0]
        self.name = "dlptest_" + uuid.uuid4().hex[:10]
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
                await c.execute(text(sql), p)
        finally:
            await e.dispose()

    async def _ablauf(self, tabelle_anlegen):
        e = create_async_engine(self.ziel)
        try:
            async with e.begin() as c:
                await c.run_sync(tabelle_anlegen)
            erster = await ensure_dlp_rules_schema(e)
            zweiter = await ensure_dlp_rules_schema(e)
            async with e.connect() as c:
                regeln = dict((await c.execute(text(
                    "SELECT pii_class, action FROM dlp_rules "
                    "WHERE agent_id IS NULL AND created_at IS NOT NULL"
                ))).fetchall())
                standards = dict((await c.execute(text(
                    "SELECT column_name, column_default FROM information_schema.columns "
                    "WHERE table_name = 'dlp_rules' AND column_name IN ('created_at', 'enabled')"
                ))).fetchall())
            return erster, zweiter, regeln, standards
        finally:
            await e.dispose()

    def _pruefe(self, ergebnis):
        erster, zweiter, regeln, standards = ergebnis
        self.assertEqual(erster, len(DEFAULT_ACTIONS))
        self.assertEqual(zweiter, 0, "Zweiter Start darf nicht erneut seeden.")
        self.assertEqual(regeln, DEFAULT_ACTIONS)
        self.assertIsNotNone(standards.get("created_at"))
        self.assertIsNotNone(standards.get("enabled"))

    def test_affected_installation_is_repaired_and_seeded(self):
        """Der gemeldete Fall: Tabelle aus dem alten Modell."""
        self._pruefe(asyncio.run(self._ablauf(
            lambda sync: _tabelle_wie_altes_modell(MetaData()).metadata.create_all(sync)
        )))

    def test_fresh_installation_from_current_model_is_seeded(self):
        self._pruefe(asyncio.run(self._ablauf(
            lambda sync: DlpRule.__table__.create(sync)
        )))


if __name__ == "__main__":
    unittest.main()
