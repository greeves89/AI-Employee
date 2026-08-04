"""Screenshots muessen IM PROZESS der Bridge entstehen, nicht in einem Fremdprozess.

Meldung 2026-08-04: „Die Bridge ist schon freigegeben, es poppt dennoch bei JEDER
Anfrage die Berechtigungsabfrage auf."

Ursache: `pyautogui.screenshot()` startet auf macOS bei jedem Aufruf das Programm
`screencapture` als eigenen Prozess. Die Freigabe zur Bildschirmaufnahme haengt an
der anfragenden Anwendung — ein kurzlebiger Fremdprozess bekommt sie nicht
zuverlaessig zugeordnet, also fragt macOS jedes Mal erneut, obwohl der Nutzer sie
laengst erteilt hat.

Geprueft wird die Quelle: der Kern (CGImage -> PIL) braucht einen echten Bildschirm
und laeuft auf keinem Build-Rechner.
"""

import unittest
from pathlib import Path

_BRIDGE = Path(__file__).resolve().parents[2] / "computer-use-bridge" / "bridge.py"
_SRC = _BRIDGE.read_text()


def _fn(name: str) -> str:
    start = _SRC.index(f"def {name}(")
    rest = _SRC[start:]
    nxt = rest.index("\ndef ", 1)
    return rest[:nxt]


class InProcessCaptureTests(unittest.TestCase):
    def test_inprocess_capture_exists(self):
        self.assertIn("def _capture_macos_inprocess(", _SRC)

    def test_macos_tries_quartz_first(self):
        code = _fn("take_screenshot")
        self.assertIn("_capture_macos_inprocess()", code)
        quartz_at = code.index("_capture_macos_inprocess()")
        pyautogui_at = code.index("import pyautogui")
        self.assertLess(quartz_at, pyautogui_at,
                        "pyautogui darf nur der Rueckfall sein, nicht der erste Weg")

    def test_pyautogui_remains_as_fallback(self):
        """Linux/Windows und ein fehlendes Quartz duerfen nicht ohne Screenshot dastehen."""
        self.assertIn("pyautogui.screenshot()", _fn("take_screenshot"))

    def test_row_padding_is_honoured(self):
        """Ohne CGImageGetBytesPerRow verscheert das Bild — der klassische Fehler
        bei CGImage -> PIL."""
        self.assertIn("CGImageGetBytesPerRow", _fn("_capture_macos_inprocess"))

    def test_bgra_is_converted(self):
        """macOS liefert BGRA; ohne die Angabe waeren Rot und Blau vertauscht."""
        self.assertIn('"BGRA"', _fn("_capture_macos_inprocess"))

    def test_quartz_is_declared_as_a_dependency(self):
        req = (_BRIDGE.parent / "requirements.txt").read_text()
        self.assertIn("pyobjc-framework-Quartz", req)


if __name__ == "__main__":
    unittest.main()
