"""Ein abgelaufener/kaputter Codex-Zugang muss VOR dem Start der CLI erkannt
werden, mit einer eigenen, sprechenden Meldung (Issue #710).

Gemessen am 06.09.2026: 16 von 16 Laeufen scheiterten mit `status=failed`,
`error="Codex CLI exited with code 1"` und leerem `result` — die CLI schrieb
in diesem Fall NICHTS auf stderr, sodass die eigentliche Ursache (abgelaufene
Anmeldedaten) aus der Plattform heraus nicht ermittelbar war. Diese Pruefung
laeuft, BEVOR die CLI ueberhaupt gestartet wird, und braucht dafuer keine
Ausgabe der CLI selbst.
"""

import base64
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from app.codex_runner import _codex_auth_problem


def _jwt(exp: int | None) -> str:
    """Minimaler, unsignierter JWT-Rohbau — nur der payload-Teil zaehlt hier."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_dict = {} if exp is None else {"exp": exp}
    payload = base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class ACodexHomeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.auth_path = os.path.join(self.tmpdir.name, "auth.json")
        self.env_patch = patch.dict(os.environ, {"CODEX_HOME": self.tmpdir.name})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _write(self, obj: dict) -> None:
        with open(self.auth_path, "w") as f:
            json.dump(obj, f)


class EinFehlenderZugangWirdBenanntTests(ACodexHomeTests):
    def test_keine_datei_vorhanden(self):
        problem = _codex_auth_problem()
        self.assertIsNotNone(problem)
        self.assertIn("fehlen", problem)
        self.assertIn(self.auth_path, problem)


class EineKaputteDateiWirdBenanntTests(ACodexHomeTests):
    def test_kein_json(self):
        with open(self.auth_path, "w") as f:
            f.write("{ das ist kein json")
        problem = _codex_auth_problem()
        self.assertIsNotNone(problem)
        self.assertIn("nicht lesbar", problem)

    def test_json_ohne_tokens(self):
        self._write({"irrelevant": True})
        problem = _codex_auth_problem()
        self.assertIsNotNone(problem)
        self.assertIn("unvollstaendig", problem)

    def test_access_token_ohne_gueltige_jwt_struktur(self):
        self._write({"tokens": {"access_token": "kein-jwt"}})
        problem = _codex_auth_problem()
        self.assertIsNotNone(problem)
        self.assertIn("unvollstaendig", problem)


class DerAblauf_wird_erkanntTests(ACodexHomeTests):
    def test_ein_abgelaufener_token_wird_benannt(self):
        self._write({"tokens": {"access_token": _jwt(int(time.time()) - 3600)}})
        problem = _codex_auth_problem()
        self.assertIsNotNone(problem)
        self.assertIn("abgelaufen", problem)

    def test_ein_gueltiger_token_meldet_kein_problem(self):
        self._write({"tokens": {"access_token": _jwt(int(time.time()) + 3600)}})
        self.assertIsNone(_codex_auth_problem())

    def test_fehlendes_exp_claim_gilt_als_unbekannt_nicht_als_abgelaufen(self):
        """Ein Token ohne exp-Claim ist nicht automatisch ungueltig — nur die
        Ablaufpruefung selbst kann darueber nichts sagen. Nicht blockieren."""
        self._write({"tokens": {"access_token": _jwt(None)}})
        self.assertIsNone(_codex_auth_problem())


if __name__ == "__main__":
    unittest.main()
