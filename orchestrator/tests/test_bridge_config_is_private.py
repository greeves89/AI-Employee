"""Die Bridge-Konfigurationsdatei enthaelt das JWT — sie gehoert nur dem Nutzer.

``~/.ai_employee_bridge.json`` traegt Token, Session und Freigabelisten. Mit
der umask-Vorgabe (0644) konnte jeder andere lokale Account das Token lesen
und sich damit als dieser Nutzer ausgeben — auf einem Klinik-Terminal mit
mehreren Konten kein theoretischer Fall. Geprueft wird die ECHTE Funktion
``save_config``, nicht eine Kopie ihrer Logik.
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import tray_app  # noqa: E402


@unittest.skipIf(sys.platform.startswith("win"), "POSIX-Rechte gibt es unter Windows nicht")
class ConfigFilePermissionTests(unittest.TestCase):
    def setUp(self):
        self._orig = tray_app.CONFIG_FILE
        self._tmp = tempfile.TemporaryDirectory()
        tray_app.CONFIG_FILE = Path(self._tmp.name) / "bridge.json"

    def tearDown(self):
        tray_app.CONFIG_FILE = self._orig
        self._tmp.cleanup()

    def test_fresh_config_is_owner_only(self):
        err = tray_app.save_config({"token": "geheim"})
        self.assertIsNone(err)
        mode = stat.S_IMODE(os.stat(tray_app.CONFIG_FILE).st_mode)
        self.assertEqual(mode, 0o600,
                         f"Config ist {oct(mode)} — das JWT darf nur der Besitzer lesen")

    def test_existing_world_readable_config_gets_tightened(self):
        """Bestandsinstallationen haben die Datei schon mit 0644 — der naechste
        Speichervorgang muss das reparieren, nicht fortschreiben."""
        tray_app.CONFIG_FILE.write_text("{}", encoding="utf-8")
        os.chmod(tray_app.CONFIG_FILE, 0o644)
        tray_app.save_config({"token": "geheim"})
        mode = stat.S_IMODE(os.stat(tray_app.CONFIG_FILE).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
