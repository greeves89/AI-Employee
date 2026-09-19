"""Nach einem gescheiterten ``alembic upgrade head`` wird nur eine frische DB gestempelt (#796).

Der Rueckfall ``create_all`` + ``stamp head`` erklaerte auf einer VERSORGTEN
Anlage alle offenen Migrationen fuer erledigt, obwohl keine gelaufen war. Beim
Melder sprang ``alembic_version`` nach einem Update von ``a1g2e3n4t5f6`` auf
head, ``agents.access_policy`` fehlte weiter, und ``upgrade head`` war beim
naechsten Start ein No-op: der Fehler war dauerhaft. Dazu wurden nur die
ersten 200 Zeichen von stderr protokolliert — ausnahmslos Alembics INFO-Zeilen.

Geprueft wird hier ZWEIERLEI: die Entscheidungslogik in
``app.core.migration_guard`` (rein, ohne Datenbank) und das echte Verhalten von
``_migrate_or_fallback`` / ``_init_db_from_models`` / ``_alembic_db_zustand``
in ``app.main`` mit gestubbter Engine und gestubbtem ``subprocess.run`` —
nicht die Quelltextform. Der Verdrahtungstest ist der wichtigste: ein
Gegenleser hat gezeigt, dass mit gut getesteten Einzelteilen und ohne ihn die
Messung des DB-Zustands still durch ``FRISCH`` ersetzt werden kann — exakt der
Bug aus dem Issue — und alles gruen bleibt.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import unittest
from contextlib import asynccontextmanager
from unittest import mock

from app.core.migration_guard import (
    DEFAULT_UPGRADE_TIMEOUT_SECONDS,
    FRISCH,
    entscheide_rueckfall,
    stderr_ende,
    upgrade_timeout_seconds,
)

_ALEMBIC_INFO = (
    "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n"
    "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
    "INFO  [alembic.runtime.migration] Running upgrade a1g2e3n4t5f6 -> h6i7j8k9l0m1\n"
)
_ECHTER_FEHLER = "sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedTable) relation \"x\" does not exist"


class EntscheidungTests(unittest.TestCase):
    def test_frische_db_wird_gestempelt(self):
        r = entscheide_rueckfall(FRISCH, timeout=False, stderr=_ALEMBIC_INFO + _ECHTER_FEHLER)
        self.assertTrue(r.frisch)
        self.assertEqual(r.stufe, logging.WARNING)

    def test_versorgte_db_wird_NICHT_gestempelt(self):
        """Der Fall des Melders: Revision steht, Upgrade scheitert."""
        r = entscheide_rueckfall("a1g2e3n4t5f6", timeout=False, stderr=_ALEMBIC_INFO + _ECHTER_FEHLER)
        self.assertFalse(r.frisch)
        self.assertEqual(r.stufe, logging.ERROR)
        self.assertIn("a1g2e3n4t5f6", r.meldung)

    def test_unbekannter_zustand_gilt_als_versorgt(self):
        """Verbindung weg: ein ausgelassener Stempel kostet einen zweiten
        Start, ein falscher Stempel die Anlage."""
        r = entscheide_rueckfall(None, timeout=False, stderr="")
        self.assertFalse(r.frisch)

    def test_nach_timeout_wird_nie_gestempelt(self):
        """Auch nicht auf der frischen DB — der Prozess koennte noch laufen."""
        for zustand in (FRISCH, "a1g2e3n4t5f6", None):
            with self.subTest(zustand=zustand):
                r = entscheide_rueckfall(zustand, timeout=True)
                self.assertFalse(r.frisch)
                self.assertEqual(r.stufe, logging.ERROR)
                self.assertIn("ALEMBIC_UPGRADE_TIMEOUT_SECONDS", r.meldung)

    def test_die_meldung_traegt_den_echten_fehler_nicht_die_info_zeilen(self):
        lang = _ALEMBIC_INFO * 40 + _ECHTER_FEHLER
        r = entscheide_rueckfall("a1g2e3n4t5f6", timeout=False, stderr=lang)
        self.assertIn(_ECHTER_FEHLER, r.meldung)


class StderrEndeTests(unittest.TestCase):
    def test_kurzer_text_bleibt_ganz(self):
        self.assertEqual(stderr_ende("  abc \n"), "abc")

    def test_langer_text_behaelt_das_ende(self):
        text = "A" * 5000 + "FEHLER"
        ende = stderr_ende(text, zeichen=100)
        self.assertTrue(ende.endswith("FEHLER"))
        self.assertLessEqual(len(ende), 101)
        self.assertNotIn("A" * 101, ende)

    def test_leer_und_none(self):
        self.assertEqual(stderr_ende(None), "")
        self.assertEqual(stderr_ende(""), "")

    def test_bytes_werden_dekodiert(self):
        self.assertEqual(stderr_ende(b"x\xc3\xa4"), "xä")


class TimeoutTests(unittest.TestCase):
    def test_standard_ist_deutlich_laenger_als_30_s(self):
        self.assertGreaterEqual(DEFAULT_UPGRADE_TIMEOUT_SECONDS, 120)
        self.assertEqual(upgrade_timeout_seconds({}), DEFAULT_UPGRADE_TIMEOUT_SECONDS)

    def test_umgebung_zaehlt(self):
        self.assertEqual(upgrade_timeout_seconds({"ALEMBIC_UPGRADE_TIMEOUT_SECONDS": "900"}), 900)

    def test_unbrauchbare_werte_fallen_auf_den_standard(self):
        for roh in ("", "abc", "0", "-5", " "):
            with self.subTest(roh=roh):
                self.assertEqual(
                    upgrade_timeout_seconds({"ALEMBIC_UPGRADE_TIMEOUT_SECONDS": roh}),
                    DEFAULT_UPGRADE_TIMEOUT_SECONDS,
                )

    def test_ohne_argument_liest_er_die_prozessumgebung(self):
        with mock.patch.dict("os.environ", {"ALEMBIC_UPGRADE_TIMEOUT_SECONDS": "77"}):
            self.assertEqual(upgrade_timeout_seconds(), 77)
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(upgrade_timeout_seconds(), DEFAULT_UPGRADE_TIMEOUT_SECONDS)


# --- Verhalten in app.main mit gestubbter Engine ------------------------------

class _Ergebnis:
    def __init__(self, wert):
        self._wert = wert

    def scalar(self):
        return self._wert

    @property
    def rowcount(self):
        return 0


class _Verbindung:
    """Antwortet auf SQL der Reihe nach; alles andere schluckt sie."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.sql: list[str] = []

    async def execute(self, stmt, *a, **kw):
        self.sql.append(str(stmt))
        if self.antworten:
            return _Ergebnis(self.antworten.pop(0))
        return _Ergebnis(None)

    async def run_sync(self, fn, *a, **kw):
        return fn(self, *a, **kw)


class _Engine:
    def __init__(self, antworten=(), *, wirft: Exception | None = None):
        self.verbindung = _Verbindung(antworten)
        self.wirft = wirft
        self.disposed = False

    @asynccontextmanager
    async def _ctx(self):
        if self.wirft is not None:
            raise self.wirft
        yield self.verbindung

    def begin(self):
        return self._ctx()

    def connect(self):
        return self._ctx()

    async def dispose(self):
        self.disposed = True


def _lauf(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class InitDbFromModelsTests(unittest.TestCase):
    """``frisch`` entscheidet, ob ``create_all`` laeuft und ``alembic stamp head`` gestartet wird."""

    def _laufen_lassen(self, frisch: bool):
        import app.main as m

        aufrufe: list[list[str]] = []
        engine = _Engine()

        def fake_run(argv, **kw):
            aufrufe.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with mock.patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=engine), \
                mock.patch("subprocess.run", side_effect=fake_run), \
                mock.patch("sqlalchemy.schema.MetaData.create_all") as create_all:
            _lauf(m._init_db_from_models(frisch=frisch))
        return aufrufe, create_all, engine

    def test_versorgt_weder_create_all_noch_stempel(self):
        """create_all legte sonst eine Tabelle an, an der die offene Migration
        beim naechsten Start mit DuplicateTable scheitert — wieder dauerhaft."""
        aufrufe, create_all, engine = self._laufen_lassen(frisch=False)
        self.assertEqual([a for a in aufrufe if "stamp" in a], [])
        create_all.assert_not_called()
        self.assertTrue(engine.disposed)

    def test_versorgt_laufen_die_idempotenten_ergaenzungen_trotzdem(self):
        """pgvector-Spalten, second_brains-Spalten usw. sind IF NOT EXISTS und
        gehoeren zu jedem Start — der Rueckfall darf sie nicht mitreissen."""
        _, _, engine = self._laufen_lassen(frisch=False)
        sql = "\n".join(engine.verbindung.sql)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS", sql)

    def test_frisch_create_all_und_stempel(self):
        aufrufe, create_all, _ = self._laufen_lassen(frisch=True)
        self.assertIn(["alembic", "stamp", "head"], aufrufe)
        create_all.assert_called_once()

    def test_frisch_ist_pflichtargument(self):
        """Ein vergessenes Argument darf nicht still zum alten Verhalten
        (stempeln) werden."""
        import inspect

        import app.main as m

        param = inspect.signature(m._init_db_from_models).parameters["frisch"]
        self.assertIs(param.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIs(param.default, inspect.Parameter.empty)


class AlembicDbZustandTests(unittest.TestCase):
    def _zustand(self, engine):
        import app.main as m

        with mock.patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=engine):
            return _lauf(m._alembic_db_zustand())

    def test_tabelle_fehlt_heisst_frisch(self):
        engine = _Engine(antworten=[None])
        self.assertEqual(self._zustand(engine), FRISCH)
        self.assertTrue(engine.disposed)
        self.assertIn("to_regclass('alembic_version')", engine.verbindung.sql[0])

    def test_ohne_schema_praefix(self):
        """Alembic legt die Tabelle unqualifiziert im ersten Schema des
        search_path an; ``public.`` faende sie dort nicht und erklaerte eine
        versorgte Anlage fuer frisch."""
        engine = _Engine(antworten=[None])
        self._zustand(engine)
        self.assertNotIn("public.", engine.verbindung.sql[0])

    def test_tabelle_leer_heisst_frisch(self):
        engine = _Engine(antworten=[12345, None])  # asyncpg liefert regclass als OID
        self.assertEqual(self._zustand(engine), FRISCH)
        self.assertIn("version_num FROM alembic_version", engine.verbindung.sql[1])

    def test_revision_wird_zurueckgegeben(self):
        engine = _Engine(antworten=[12345, "a1g2e3n4t5f6"])
        self.assertEqual(self._zustand(engine), "a1g2e3n4t5f6")

    def test_verbindungsfehler_heisst_unbekannt_nicht_frisch(self):
        """Sonst wuerde eine kurz nicht erreichbare DB als frisch gelten — und gestempelt."""
        engine = _Engine(wirft=ConnectionError("db weg"))
        self.assertIsNone(self._zustand(engine))
        self.assertTrue(engine.disposed)


class MigrateOrFallbackTests(unittest.TestCase):
    """Die Verdrahtung: gemessener Zustand -> Entscheidung -> ``frisch`` durchgereicht.

    ``subprocess.run`` wird gestubbt (kein alembic im Test), ``_alembic_db_zustand``
    und ``_init_db_from_models`` ebenfalls — geprueft wird, WAS der Start mit
    dem Messwert macht."""

    def _start(self, zustand, *, returncode=None, timeout=False, env=None):
        import app.main as m

        gemessen = {"n": 0}

        async def fake_zustand():
            gemessen["n"] += 1
            return zustand

        init_aufrufe: list[dict] = []

        async def fake_init(**kw):
            init_aufrufe.append(kw)

        run_aufrufe: list[dict] = []

        def fake_run(argv, **kw):
            run_aufrufe.append({"argv": list(argv), **kw})
            if timeout:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout"), stderr=b"INFO ...")
            return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=_ALEMBIC_INFO + _ECHTER_FEHLER)

        with mock.patch.object(m, "_alembic_db_zustand", fake_zustand), \
                mock.patch.object(m, "_init_db_from_models", fake_init), \
                mock.patch("subprocess.run", side_effect=fake_run), \
                mock.patch.dict("os.environ", env or {}, clear=False), \
                self.assertLogs("app.main", level="INFO") as logs:
            _lauf(m._migrate_or_fallback())
        return gemessen["n"], run_aufrufe, init_aufrufe, logs

    def test_erfolg_ruft_keinen_rueckfall(self):
        n, run, init, _ = self._start("a1g2e3n4t5f6", returncode=0)
        self.assertEqual(n, 1)
        self.assertEqual(run[0]["argv"], ["alembic", "upgrade", "head"])
        self.assertEqual(init, [])

    def test_versorgt_und_fehlschlag_wird_NICHT_als_frisch_behandelt(self):
        """Der Bug des Issues, Ende-zu-Ende: Revision steht, Upgrade scheitert."""
        _, _, init, logs = self._start("a1g2e3n4t5f6", returncode=1)
        self.assertEqual(init, [{"frisch": False}])
        fehler = [r for r in logs.records if r.levelno == logging.ERROR]
        self.assertEqual(len(fehler), 1)
        self.assertIn(_ECHTER_FEHLER, fehler[0].getMessage())
        self.assertIn("a1g2e3n4t5f6", fehler[0].getMessage())

    def test_frisch_und_fehlschlag_wird_als_frisch_behandelt(self):
        _, _, init, logs = self._start(FRISCH, returncode=1)
        self.assertEqual(init, [{"frisch": True}])
        self.assertEqual([r.levelno for r in logs.records if r.levelno >= logging.WARNING], [logging.WARNING])

    def test_unbekannt_und_fehlschlag_wird_nicht_als_frisch_behandelt(self):
        _, _, init, _ = self._start(None, returncode=1)
        self.assertEqual(init, [{"frisch": False}])

    def test_timeout_wird_nie_als_frisch_behandelt(self):
        for zustand in (FRISCH, "a1g2e3n4t5f6", None):
            with self.subTest(zustand=zustand):
                _, _, init, logs = self._start(zustand, timeout=True)
                self.assertEqual(init, [{"frisch": False}])
                self.assertTrue(any(r.levelno == logging.ERROR for r in logs.records))

    def test_der_zustand_wird_VOR_dem_upgrade_gemessen(self):
        """Nach einem halb gelaufenen Upgrade koennte die Tabelle schon da sein."""
        import app.main as m

        reihenfolge: list[str] = []

        async def fake_zustand():
            reihenfolge.append("messen")
            return "a1g2e3n4t5f6"

        async def fake_init(**kw):
            reihenfolge.append("init")

        def fake_run(argv, **kw):
            reihenfolge.append("upgrade")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="x")

        with mock.patch.object(m, "_alembic_db_zustand", fake_zustand), \
                mock.patch.object(m, "_init_db_from_models", fake_init), \
                mock.patch("subprocess.run", side_effect=fake_run), \
                self.assertLogs("app.main", level="ERROR"):
            _lauf(m._migrate_or_fallback())
        self.assertEqual(reihenfolge, ["messen", "upgrade", "init"])

    def test_der_timeout_kommt_aus_der_umgebung_nicht_mehr_30(self):
        _, run, _, _ = self._start("a1g2e3n4t5f6", returncode=0,
                                   env={"ALEMBIC_UPGRADE_TIMEOUT_SECONDS": "900"})
        self.assertEqual(run[0]["timeout"], 900)
        with mock.patch.dict("os.environ", {}, clear=True):
            _, run, _, _ = self._start("a1g2e3n4t5f6", returncode=0)
        self.assertEqual(run[0]["timeout"], DEFAULT_UPGRADE_TIMEOUT_SECONDS)
        self.assertNotEqual(run[0]["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
