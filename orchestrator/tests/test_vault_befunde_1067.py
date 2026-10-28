"""Befunde aus dem Review von #1067 (Hybrid-Suche): Neuindex ohne Embedding-Dienst,
abgebrochene Sitzung nach einem Datenbankfehler, Dateirechte bei Agenten-Schreibzugriffen."""

import os
import tempfile
import unittest
from unittest import mock

from app.core import vault
from app.services import vault_indexer


class _Zeilen:
    def __init__(self, zeilen):
        self._z = zeilen

    def first(self):
        return self._z[0] if self._z else None

    def scalars(self):
        return self

    def all(self):
        return self._z


class _DB:
    """Schreibt jede SQL-Anweisung mit; Antworten je nach Anfrage."""

    def __init__(self, hash_, leere_idx):
        self.sql: list[str] = []
        self.hash_, self.leere_idx = hash_, leere_idx
        self.rollbacks = self.commits = 0

    async def execute(self, stmt, params=None):
        text = str(stmt)
        self.sql.append(text)
        if "SELECT file_hash" in text:
            return _Zeilen([(self.hash_,)])
        if "embedding IS NULL LIMIT 1" in text:
            return _Zeilen([(1,)])
        if "SELECT chunk_idx" in text:
            return _Zeilen(list(self.leere_idx))
        return _Zeilen([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class NeuindexOhneEmbeddings(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "a.md"), "w") as f:
            f.write("# Titel\n\nInhalt der Notiz.")
        self.hash_ = vault_indexer._file_hash(vault.read_file(self.tmp, "a.md"))

    async def test_unveraenderte_datei_wird_nicht_neu_geschrieben(self):
        db = _DB(self.hash_, [0])
        zustand: dict = {}
        with mock.patch.object(vault_indexer, "_embed_chunks", mock.AsyncMock(return_value=[None])):
            await vault_indexer.index_file(db, "b", self.tmp, "a.md", zustand)
        self.assertFalse(any("DELETE" in s or "INSERT" in s for s in db.sql))
        self.assertTrue(zustand.get("aus"))

    async def test_ohne_dienst_fragt_die_naechste_datei_nicht_mehr(self):
        db = _DB(self.hash_, [0])
        einbetten = mock.AsyncMock(return_value=[None])
        with mock.patch.object(vault_indexer, "_embed_chunks", einbetten):
            await vault_indexer.index_file(db, "b", self.tmp, "a.md", {"aus": True})
        einbetten.assert_not_awaited()

    async def test_mit_dienst_nur_die_fehlenden_setzen(self):
        db = _DB(self.hash_, [0])
        with mock.patch.object(vault_indexer, "_embed_chunks", mock.AsyncMock(return_value=[[0.1, 0.2]])):
            await vault_indexer.index_file(db, "b", self.tmp, "a.md", {})
        self.assertTrue(any("UPDATE vault_chunks SET embedding" in s for s in db.sql))
        self.assertFalse(any("DELETE" in s for s in db.sql))


class FehlerReisstDenLaufNichtMit(unittest.IsolatedAsyncioTestCase):
    async def test_nach_einem_fehler_wird_zurueckgerollt_und_weitergemacht(self):
        tmp = tempfile.mkdtemp()
        for name in ("a.md", "b.md"):
            with open(os.path.join(tmp, name), "w") as f:
                f.write("x")
        db = _DB("", [])
        aufrufe = []

        async def index(db_, label, pfad, rel, zustand=None):
            aufrufe.append(rel)
            if len(aufrufe) == 1:
                raise RuntimeError("NUL-Byte")
            return 1

        with mock.patch.object(vault_indexer, "index_file", index):
            stats = await vault_indexer.reindex_vault(db, "b", tmp)
        self.assertEqual((stats["errors"], stats["files"], db.rollbacks), (1, 1, 1))

    async def test_nul_byte_kommt_nicht_in_die_datenbank(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "n.md"), "w") as f:
            f.write("vor\x00nach")
        db = _DB("anderer-hash", [])
        with mock.patch.object(vault_indexer, "_embed_chunks", mock.AsyncMock(side_effect=lambda t: [None] * len(t))):
            await vault_indexer.index_file(db, "b", tmp, "n.md", {})
        self.assertTrue(any("INSERT" in s for s in db.sql))


class DateirechteBeimSchreiben(unittest.TestCase):
    def test_neue_ordner_und_datei_gehoeren_dem_agenten(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(vault.os, "chown") as chown:
            vault.write_file(tmp, "neu/tief/notiz.md", "Text")
        pfade = [c.args[0] for c in chown.call_args_list]
        self.assertIn(os.path.join(os.path.realpath(tmp), "neu"), pfade)
        self.assertIn(os.path.join(os.path.realpath(tmp), "neu", "tief"), pfade)
        self.assertIn(os.path.join(os.path.realpath(tmp), "neu", "tief", "notiz.md"), pfade)
        self.assertTrue(all(c.args[1:] == (vault.AGENT_UID, vault.AGENT_UID) for c in chown.call_args_list))

    def test_ohne_root_schlaegt_nichts_fehl(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(vault.os, "chown", side_effect=PermissionError):
            ergebnis = vault.write_file(tmp, "x.md", "Text")
        self.assertTrue(ergebnis["created"])
