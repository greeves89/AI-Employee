"""Die Bridge ist auf dem Mac eine echte App: Dock, Cmd+Tab, App-Menue, normale Fenster.

Vorher: reine Menueleisten-App (LSUIElement), Fenster als Panels — nach einem
App-Wechsel war das Fenster „einfach weg“ und ueber Cmd+Tab nicht zu finden.
"""

import re
import sys
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIER))
import tray_app  # noqa: E402


class Bundle(unittest.TestCase):
    def test_nicht_mehr_nur_menueleiste(self):
        spec = (HIER / "bridge_macos.spec").read_text(encoding="utf-8")
        self.assertRegex(spec, r"'LSUIElement':\s*False")


@unittest.skipUnless(sys.platform == "darwin", "AppKit nur auf dem Mac")
class MacMenueUndFenster(unittest.TestCase):
    def setUp(self):
        try:
            import AppKit  # noqa: F401
        except ImportError:
            self.skipTest("AppKit fehlt")

    def test_app_menue_mit_beenden_und_fenster(self):
        from AppKit import NSApplication
        tray_app._install_edit_menu()
        app = NSApplication.sharedApplication()
        self.assertEqual(app.activationPolicy(), 0, "regulaer: Dock und Cmd+Tab")
        menue = app.mainMenu()
        titel = [str(menue.itemAtIndex_(i).submenu().title()) for i in range(menue.numberOfItems())]
        self.assertEqual(titel, ["AI-Employee Bridge", "Bearbeiten", "Fenster"])
        app_menue = menue.itemAtIndex_(0).submenu()
        beenden = [app_menue.itemAtIndex_(i) for i in range(app_menue.numberOfItems())
                   if str(app_menue.itemAtIndex_(i).keyEquivalent()) == "q"]
        self.assertEqual(len(beenden), 1)
        bearbeiten = menue.itemAtIndex_(1).submenu()
        self.assertIn("v", [str(bearbeiten.itemAtIndex_(i).keyEquivalent()) for i in range(bearbeiten.numberOfItems())])

    def test_hauptfenster_ist_ein_normales_fenster(self):
        from AppKit import NSPanel
        f = tray_app._make_fenster("Test", 300, 200)
        self.assertNotIsInstance(f, NSPanel)
        self.assertTrue(f.styleMask() & (1 << 2), "minimierbar")


if __name__ == "__main__":
    unittest.main()
