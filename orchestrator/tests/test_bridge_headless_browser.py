"""Der Browser der Bridge kann unsichtbar im Hintergrund laufen (wie ego lite).

Wunsch des Nutzers 2026-08-18: die Browsersteuerung „im Hintergrund", so wie
der ego-lite-Browser — der Agent bedient die Seite ueber den DOM, ohne dass ein
Fenster den Vordergrund kapert.

Umgesetzt als Opt-in ueber die Konfiguration (``browser_headless``), gesetzt vom
Berechtigungs-Dialog. Standard bleibt SICHTBAR: eine eingeloggte Sitzung
unsichtbar laufen zu lassen soll eine bewusste Entscheidung sein.

Geprueft wird, dass der Launch-Aufruf den Wert aus der Konfiguration liest —
gegen einen Fake-Playwright, der nur die kwargs mitschreibt, ohne echten
Browser zu starten.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import bridge  # noqa: E402


class _FakeContext:
    def __init__(self):
        self.pages = []

    def close(self):
        pass


class _FakeChromium:
    def __init__(self, sink):
        self._sink = sink

    def launch_persistent_context(self, **kwargs):
        self._sink.append(kwargs)
        return _FakeContext()


class _FakePlaywright:
    def __init__(self, sink):
        self.chromium = _FakeChromium(sink)

    def stop(self):
        pass


class HeadlessBrowserTests(unittest.TestCase):
    def setUp(self):
        self._orig_config = bridge.BRIDGE_CONFIG_PATH
        self._tmp = tempfile.TemporaryDirectory()
        bridge.BRIDGE_CONFIG_PATH = os.path.join(self._tmp.name, "bridge.json")

    def tearDown(self):
        bridge.BRIDGE_CONFIG_PATH = self._orig_config
        self._tmp.cleanup()

    def _write(self, cfg):
        with open(bridge.BRIDGE_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def _launch_kwargs(self) -> list[dict]:
        """Den echten _run() mit einem Fake-Playwright fahren und die kwargs
        des ersten erfolgreichen Launch zurueckgeben.

        Playwright ist im Test-venv nicht installiert; _run() importiert es
        aber intern (`from playwright.sync_api import sync_playwright`). Wir
        schieben ein Fake-Modul in sys.modules, damit genau dieser Import
        unser Fake trifft — ohne echten Browser.
        """
        import types
        sink: list[dict] = []
        fake_mod = types.ModuleType("playwright")
        fake_sync = types.ModuleType("playwright.sync_api")
        fake_sync.sync_playwright = lambda: type(
            "S", (), {"start": staticmethod(lambda: _FakePlaywright(sink))})()
        saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
        sys.modules["playwright"] = fake_mod
        sys.modules["playwright.sync_api"] = fake_sync
        try:
            bc = bridge.BrowserController(profile_dir=os.path.join(self._tmp.name, "prof"))
            # _run laeuft in einer Endlosschleife auf der Queue; wir stellen ein
            # Beenden-Signal ein, damit es nach dem Launch zurueckkehrt.
            bc._queue.put((None, []))
            bc._run()
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return sink

    def test_default_is_visible(self):
        self._write({})
        kwargs = self._launch_kwargs()
        self.assertTrue(kwargs, "Kein Launch erfolgt")
        self.assertFalse(kwargs[0]["headless"],
                         "Ohne Opt-in muss der Browser SICHTBAR starten")

    def test_opt_in_makes_it_headless(self):
        self._write({"browser_headless": True})
        kwargs = self._launch_kwargs()
        self.assertTrue(kwargs[0]["headless"],
                        "browser_headless=true muss den Browser unsichtbar starten")

    def test_still_uses_own_profile_dir(self):
        """Headless aendert nichts am eigenen, privaten Profil — kein Cookie-Klau
        aus dem Nutzerprofil."""
        self._write({"browser_headless": True})
        kwargs = self._launch_kwargs()
        self.assertIn("user_data_dir", kwargs[0])
        self.assertIn("prof", kwargs[0]["user_data_dir"])


if __name__ == "__main__":
    unittest.main()
