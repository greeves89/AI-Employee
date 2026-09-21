"""Migrationen gegen ein ECHTES PostgreSQL — so, wie eine gewachsene Anlage sie faehrt.

Warum es diesen Test gibt: Am 21.09.2026 stand bei einer Kundenanlage die
komplette API still. Die Migration zu ``agents.access_policy`` prueft mit
``config ? 'schluessel'``, ob ein Agent die alten Berechtigungs-Schluessel
traegt. Den ``?``-Operator gibt es in PostgreSQL nur fuer ``jsonb``;
``agents.config`` ist ``json``. Bei transaktionalem DDL rollt der Fehler die
GANZE Migration zurueck — auch die Spalte, die sie anlegen sollte. Der
Orchestrator fragt beim Start die Agenten ab, fand die Spalte nicht und
beendete sich mit ``Application startup failed``.

**Warum kein bestehender Test das gefunden hat — und was daraus folgt:**

Die Migrationskette legt das Schema NICHT an. Die Tabellen entstehen aus den
Modellen (``Base.metadata.create_all``), Migrationen aendern nur Vorhandenes.
Auf einer frisch aufgesetzten Anlage wird Alembic deshalb nur auf den Stand
gestempelt — die Spalte ist da, ohne dass die Migration je lief. Genau so
laeuft auch die Testumgebung.

Ein Test, der die Kette auf eine LEERE Datenbank loslaesst, hilft nicht: Sie
scheitert dort schon an der ersten Migration (``relation "agents" does not
exist``) und prueft damit etwas, das im Betrieb nie vorkommt.

Dieser Test baut deshalb den echten Kundenzustand nach:
  1. Schema aus den Modellen anlegen,
  2. die neue Spalte wieder entfernen und einen Agenten mit den ALTEN
     Schluesseln einsetzen,
  3. Alembic auf den Stand DAVOR stempeln,
  4. nach vorn migrieren — und pruefen, dass die Daten wirklich umgezogen sind.

Bewusst gegen echtes PostgreSQL: Der Fehler war ein Operator-/Typfehler, den
SQLite nie gemeldet haette, weil es weder ``?`` noch ``jsonb`` kennt. Ohne
erreichbares PostgreSQL wird uebersprungen; in der CI steht der Dienst bereit.
"""

import asyncio
import os
import subprocess
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_DB_URL = os.environ.get("DATABASE_URL", "")

#: Revision unmittelbar VOR der access_policy-Migration. Von hier aus wird
#: nach vorn migriert — das ist der Sprung, an dem die Kundenanlage stand.
_REVISION_DAVOR = "7a9c2e4f1b3d"

#: Verzeichnis mit alembic.ini (orchestrator/).
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


@unittest.skipUnless(
    _postgres_da(),
    "Kein erreichbares PostgreSQL (DATABASE_URL) — in der CI laeuft dieser Test.",
)
class MigrationAufGewachsenerDatenbank(unittest.TestCase):

    def setUp(self):
        self.basis = _DB_URL.rsplit("/", 1)[0]
        self.name = "migtest_" + uuid.uuid4().hex[:10]
        self.ziel = f"{self.basis}/{self.name}"
        asyncio.run(self._admin(f'CREATE DATABASE "{self.name}"'))

    def tearDown(self):
        # Offene Verbindungen kappen, sonst bleibt die Wegwerf-Datenbank
        # stehen und der naechste Lauf stolpert ueber die Leiche.
        asyncio.run(self._admin(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :n AND pid <> pg_backend_pid()", n=self.name))
        asyncio.run(self._admin(f'DROP DATABASE IF EXISTS "{self.name}"'))

    # --- Hilfen ------------------------------------------------------------

    async def _admin(self, sql, **p):
        e = create_async_engine(f"{self.basis}/postgres", isolation_level="AUTOCOMMIT")
        try:
            async with e.connect() as c:
                r = await c.execute(text(sql), p)
                return r.fetchall() if r.returns_rows else None
        finally:
            await e.dispose()

    async def _schema_wie_gewachsene_anlage(self):
        """Schema aus den Modellen, danach auf den Zustand VOR der Migration zurueck."""
        os.environ["DATABASE_URL"] = self.ziel   # Modelle lesen die Einstellung beim Import
        from app.models import Base

        e = create_async_engine(self.ziel)
        try:
            async with e.begin() as c:
                await c.run_sync(Base.metadata.create_all)
            async with e.begin() as c:
                await c.execute(text("ALTER TABLE agents DROP COLUMN IF EXISTS access_policy"))

                # Pflichtspalten aus dem Schema fuellen, statt sich durch
                # NOT-NULL-Fehler zu raten.
                r = await c.execute(text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name='agents' AND is_nullable='NO' AND column_default IS NULL"
                ))
                werte = {
                    "id": "'t1'",
                    "name": "'Testagent'",
                    "config": ('\'{"autonomy_matrix": {"bash": true}, '
                               '"permissions_mode": "custom"}\'::json'),
                }
                for spalte, typ in r.fetchall():
                    if spalte in werte:
                        continue
                    if "char" in typ or "text" in typ or typ == "USER-DEFINED":
                        werte[spalte] = {"mode": "'claude_code'", "state": "'STOPPED'"}.get(spalte, "'x'")
                    elif "int" in typ:
                        werte[spalte] = "0"
                    elif "bool" in typ:
                        werte[spalte] = "false"
                    elif "timestamp" in typ:
                        werte[spalte] = "now()"
                    else:
                        werte[spalte] = "NULL"
                await c.execute(text(
                    f"INSERT INTO agents ({', '.join(werte)}) "
                    f"VALUES ({', '.join(werte.values())})"
                ))
        finally:
            await e.dispose()

    def _alembic(self, *args):
        """Alembic in EIGENEM Prozess.

        ``alembic/env.py`` setzt die URL selbst aus ``settings.database_url``
        und ueberschreibt damit alles, was man der Config mitgibt. Die
        Umgebungsvariable in einem eigenen Prozess ist der einzige Hebel —
        sonst laeuft der Test unbemerkt gegen die ECHTE Datenbank und meldet
        Erfolg, ohne irgendetwas ausgefuehrt zu haben.
        """
        return subprocess.run(
            ["alembic", *args], cwd=_APP_DIR, capture_output=True, text=True,
            env={**os.environ, "DATABASE_URL": self.ziel},
        )

    async def _lies(self, sql):
        e = create_async_engine(self.ziel)
        try:
            async with e.connect() as c:
                return (await c.execute(text(sql))).scalar()
        finally:
            await e.dispose()

    # --- Der eigentliche Test ---------------------------------------------

    def test_migration_laeuft_und_zieht_die_daten_um(self):
        asyncio.run(self._schema_wie_gewachsene_anlage())
        self._alembic("stamp", _REVISION_DAVOR)

        ergebnis = self._alembic("upgrade", "heads")
        ausgabe = ergebnis.stdout + ergebnis.stderr
        self.assertEqual(
            ergebnis.returncode, 0,
            f"Migration abgebrochen:\n{ausgabe[-1500:]}",
        )
        self.assertIn("Running upgrade", ausgabe,
                      "Es lief keine einzige Migration — der Test prueft dann nichts.")

        # Spalte da …
        self.assertEqual(asyncio.run(self._lies(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='agents' AND column_name='access_policy'"
        )), 1, "access_policy fehlt nach der Migration")

        # … und die Daten wirklich umgezogen, nicht nur die Spalte angelegt.
        policy = asyncio.run(self._lies("SELECT access_policy::text FROM agents WHERE id='t1'"))
        self.assertIn("autonomy_matrix", policy or "")
        self.assertIn("permissions_mode", policy or "")

        # Die alten Schluessel sind raus — sonst gaebe es wieder zwei Wahrheiten,
        # genau das Problem, das die Migration beheben soll.
        config = asyncio.run(self._lies("SELECT config::text FROM agents WHERE id='t1'"))
        self.assertNotIn("autonomy_matrix", config or "")

    def test_der_operator_der_es_zerlegt_hat(self):
        """Haelt fest, WARUM die Umwandlung noetig ist.

        ``agents.config`` ist ``json``. Ein ``?`` direkt darauf ist ein
        Typfehler; nur ueber ``::jsonb`` geht es. Wer die Umwandlung wieder
        entfernt, faellt hier auf — und nicht erst bei einer Kundenanlage.
        """
        import sqlalchemy

        asyncio.run(self._schema_wie_gewachsene_anlage())

        self.assertEqual(asyncio.run(self._lies(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='agents' AND column_name='config'"
        )), "json", "Annahme dieses Tests geaendert — bitte pruefen")

        # Mit Umwandlung: laeuft.
        asyncio.run(self._lies("SELECT count(*) FROM agents WHERE config::jsonb ? 'x'"))

        # Ohne: genau der Fehler, der die Anlage lahmgelegt hat.
        with self.assertRaises(sqlalchemy.exc.ProgrammingError):
            asyncio.run(self._lies("SELECT count(*) FROM agents WHERE config ? 'x'"))


if __name__ == "__main__":
    unittest.main()
