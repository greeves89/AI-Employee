"""Der Container muss eine Token-Erneuerung zurueckmelden — sonst ist sie weg.

Die Codex-CLI tauscht den einmaligen Refresh-Token bei jeder Erneuerung gegen
einen neuen und schreibt ihn nur in die ``auth.json`` IM Container. Der Container
lief aber mit einer Abschrift aus der Datenbank: ohne Rueckweg spielt der
naechste Start die verbrauchte Fassung erneut ein und der Anbieter antwortet ab
da mit ``refresh_token_reused`` (Issue #646).

Drei Dinge muessen dabei sitzen: gemeldet wird nur der **eigene** Zugang (den
gemeinsamen der Anlage pflegt der Orchestrator), gemeldet wird nur bei
**tatsaechlicher** Aenderung, und ein Fehlschlag darf die Meldung nicht
verbrennen — sonst faellt eine Erneuerung durchs Raster, weil der erste Versuch
zufaellig ins Netz lief.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import codex_auth_sync


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.headers: dict = {}

    def json(self) -> dict:
        return {}


class _Client:
    """Ersetzt httpx.AsyncClient und merkt sich die Aufrufe."""

    calls: list = []
    status = 200

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _Client.calls.append({"url": url, "json": json, "headers": headers})
        return _Response(_Client.status)


class PushIfRotatedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._env = patch.dict(os.environ, {"CODEX_HOME": self._dir.name})
        self._env.start()
        self.addCleanup(self._env.stop)
        _Client.calls = []
        _Client.status = 200
        self._http = patch("httpx.AsyncClient", _Client)
        self._http.start()
        self.addCleanup(self._http.stop)

    def _write(self, refresh: str) -> None:
        with open(codex_auth_sync.auth_path(), "w", encoding="utf-8") as fh:
            json.dump({"tokens": {"refresh_token": refresh}}, fh)

    async def test_gemeinsamer_zugang_wird_nie_zurueckgemeldet(self):
        """Den gemeinsamen Zugang pflegt der Orchestrator — ein Agent fasst ihn nicht an."""
        self._write("alt")
        codex_auth_sync.record_start("shared")
        self._write("neu")
        self.assertFalse(await codex_auth_sync.push_if_rotated())
        self.assertEqual(_Client.calls, [])

    async def test_ohne_aenderung_kein_aufruf(self):
        self._write("alt")
        codex_auth_sync.record_start("own")
        self.assertFalse(await codex_auth_sync.push_if_rotated())
        self.assertEqual(_Client.calls, [])

    async def test_erneuerung_wird_gemeldet(self):
        self._write("alt")
        codex_auth_sync.record_start("own")
        self._write("neu")

        self.assertTrue(await codex_auth_sync.push_if_rotated())
        self.assertEqual(len(_Client.calls), 1)
        call = _Client.calls[0]
        self.assertTrue(call["url"].endswith("/api/v1/agent-codex-auth"))
        self.assertEqual(call["json"]["auth_json"]["tokens"]["refresh_token"], "neu")
        # Die Identitaet steckt im Agenten-Token, nicht in der Nutzlast.
        self.assertIn("X-Agent-ID", call["headers"])
        self.assertTrue(call["headers"]["Authorization"].startswith("Bearer "))

        # Zweiter Lauf ohne weitere Aenderung meldet nicht erneut.
        self.assertFalse(await codex_auth_sync.push_if_rotated())
        self.assertEqual(len(_Client.calls), 1)

    async def test_abgelehnte_meldung_wird_beim_naechsten_lauf_wiederholt(self):
        """Sonst verbrennt ein einzelner Netz-/Serverfehler die Erneuerung endgueltig."""
        self._write("alt")
        codex_auth_sync.record_start("own")
        self._write("neu")

        _Client.status = 500
        self.assertFalse(await codex_auth_sync.push_if_rotated())

        _Client.status = 200
        self.assertTrue(await codex_auth_sync.push_if_rotated())
        self.assertEqual(len(_Client.calls), 2)

    async def test_unlesbare_datei_scheitert_still(self):
        self._write("alt")
        codex_auth_sync.record_start("own")
        with open(codex_auth_sync.auth_path(), "w", encoding="utf-8") as fh:
            fh.write("{kein json")
        self.assertFalse(await codex_auth_sync.push_if_rotated())
        self.assertEqual(_Client.calls, [])


if __name__ == "__main__":
    unittest.main()
