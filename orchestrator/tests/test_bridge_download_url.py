"""Der Bridge-Download-Link muss auf ein echtes Repository zeigen.

Befund 2026-08-18: Der Knopf fuehrte auf
``https://github.com//releases/download/bridge-latest/...`` — doppelter
Schraegstrich, kein Repository, 404. Ursache war das Zusammenspiel zweier
harmloser Zeilen:

* ``docker-compose.yml`` reicht ``GITHUB_REPO: ${GITHUB_REPO:-}`` weiter, setzt
  die Variable also auf einen LEEREN Wert, wenn der Host sie nicht kennt.
* ``os.getenv("GITHUB_REPO", "greeves89/AI-Employee")`` greift nur, wenn die
  Variable GAR NICHT existiert. Eine leere Variable schlaegt den Standard.

Beides fuer sich sah richtig aus. Deshalb prueft dieser Test genau den Fall
"gesetzt, aber leer" — nicht nur "nicht gesetzt".
"""

import importlib
import unittest
from unittest import mock


def _reload_with_env(**env):
    """downloads.py neu laden — die Werte werden beim Import ausgewertet."""
    with mock.patch.dict("os.environ", env, clear=False):
        import app.api.downloads as downloads
        return importlib.reload(downloads)


class BridgeDownloadUrlTests(unittest.TestCase):
    def tearDown(self):
        # Wieder in den Zustand der echten Umgebung bringen, damit andere Tests
        # nicht die hier gesetzten Werte sehen.
        import app.api.downloads as downloads
        importlib.reload(downloads)

    def test_empty_env_falls_back_to_the_real_repo(self):
        """Der eigentliche Fehler: leer gesetzt ist nicht dasselbe wie ungesetzt."""
        downloads = _reload_with_env(GITHUB_REPO="", BRIDGE_RELEASE_TAG="")
        self.assertEqual(downloads.GITHUB_REPO, "greeves89/AI-Employee")
        self.assertEqual(downloads.BRIDGE_TAG, "bridge-latest")

    def test_unset_env_falls_back_to_the_real_repo(self):
        import os
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("GITHUB_REPO", None)
            os.environ.pop("BRIDGE_RELEASE_TAG", None)
            import app.api.downloads as downloads
            downloads = importlib.reload(downloads)
            self.assertEqual(downloads.GITHUB_REPO, "greeves89/AI-Employee")

    def test_explicit_value_still_wins(self):
        """Ein eigener Fork/Tag muss weiterhin ueberschreiben koennen."""
        downloads = _reload_with_env(GITHUB_REPO="acme/fork", BRIDGE_RELEASE_TAG="v9")
        self.assertEqual(downloads.GITHUB_REPO, "acme/fork")
        self.assertEqual(downloads.BRIDGE_TAG, "v9")

    def test_built_url_never_contains_a_double_slash(self):
        """Das Symptom, das der Nutzer gesehen hat — direkt geprueft."""
        downloads = _reload_with_env(GITHUB_REPO="", BRIDGE_RELEASE_TAG="")
        url = (f"https://github.com/{downloads.GITHUB_REPO}/releases/download/"
               f"{downloads.BRIDGE_TAG}/AI-Employee-Bridge-Windows.zip")
        self.assertNotIn("github.com//", url)
        self.assertNotIn("//releases", url)
        self.assertIn("github.com/greeves89/AI-Employee/releases", url)

    def test_compose_does_not_default_the_repo_to_empty(self):
        """Zweite Haelfte der Ursache: compose darf die Variable nicht leer
        durchreichen, sonst haengt alles an der Robustheit des Python-Codes."""
        from pathlib import Path
        compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("GITHUB_REPO: ${GITHUB_REPO:-}", compose)


if __name__ == "__main__":
    unittest.main()
