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
        # Die eigentliche Aufnahme liegt seit dem Retina-Klick-Fix in
        # capture_screenshot; take_screenshot ist nur noch ein duenner Wrapper.
        code = _fn("capture_screenshot")
        self.assertIn("_capture_macos_inprocess()", code)
        quartz_at = code.index("_capture_macos_inprocess()")
        pyautogui_at = code.index("import pyautogui")
        self.assertLess(quartz_at, pyautogui_at,
                        "pyautogui darf nur der Rueckfall sein, nicht der erste Weg")

    def test_pyautogui_remains_as_fallback(self):
        """Linux/Windows und ein fehlendes Quartz duerfen nicht ohne Screenshot dastehen."""
        self.assertIn("pyautogui.screenshot()", _fn("capture_screenshot"))

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


class MissingPermissionIsReportedTests(unittest.TestCase):
    """Fehlende Bildschirmaufnahme darf kein „gueltiger" Screenshot sein.

    Ohne die Freigabe liefert macOS KEINEN Fehler, sondern ein Bild mit Schreibtisch
    und Menueleiste, aber ohne Fensterinhalte. Das sieht nach einem echten Screenshot
    aus — im Test hat es sowohl den Nutzer als auch das auswertende Modell getaeuscht
    ("ein Safari-Fenster mit einem Landschaftsfoto"; es war der Hintergrund).
    """

    def test_permission_is_checked_before_capturing(self):
        code = _fn("_capture_macos_inprocess")
        self.assertIn("CGPreflightScreenCaptureAccess", code)

    def test_missing_permission_raises_instead_of_returning_an_image(self):
        code = _fn("_capture_macos_inprocess")
        self.assertIn("ScreenRecordingPermissionError", code)

    def test_permission_error_is_not_swallowed_by_the_fallback(self):
        """Der pyautogui-Rueckfall zeigt dasselbe leere Bild — er darf hier nicht greifen."""
        code = _fn("_capture_macos_inprocess")
        self.assertIn("except ScreenRecordingPermissionError:", code)
        self.assertIn("raise", code)

    def test_action_returns_a_readable_error(self):
        self.assertIn('return {"ok": False, "error": str(e)}', _SRC)

    def test_message_names_the_exact_remedy(self):
        self.assertIn("Datenschutz & Sicherheit", _SRC)
        self.assertIn("neu starten", _SRC)
