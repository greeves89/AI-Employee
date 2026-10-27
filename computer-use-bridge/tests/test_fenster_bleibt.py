"""Fenster der Bridge verstecken sich nicht beim App-Wechsel (macOS).

NSPanel hat ``hidesOnDeactivate`` — nach Cmd+Tab war das Fenster weg, und die
Aktualisierung (nur fuer sichtbare Fenster) liess es auf „Verbinde…“ stehen.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tray_app  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "AppKit nur auf dem Mac")
class PanelBleibt(unittest.TestCase):
    def test_panel_versteckt_sich_nicht(self):
        try:
            import AppKit  # noqa: F401
        except ImportError:
            self.skipTest("AppKit fehlt")
        p = tray_app._make_panel("Test", 200, 100)
        self.assertFalse(p.hidesOnDeactivate())


if __name__ == "__main__":
    unittest.main()
