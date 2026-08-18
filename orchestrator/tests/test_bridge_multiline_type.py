"""Mehrzeiliger Text muss tippbar sein — Zeilenumbruch als Return-Taste.

Ein rohes ``\\n`` INNERHALB eines AppleScript-String-Literals ist ein
Syntaxfehler: jede E-Mail, jedes mehrzeilige Formularfeld liess ``osascript``
scheitern. Der stille Rueckfall tippte dann mit pyautogui weiter — das sendet
TASTENPOSITIONEN statt Zeichen, auf einer deutschen Tastatur wird aus ``-``
ein ``ß``. Der Agent meldete Erfolg, im Feld stand Kauderwelsch.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import bridge  # noqa: E402


class KeystrokeScriptTests(unittest.TestCase):
    def test_newlines_become_return_keystrokes(self):
        script = bridge._keystroke_script("Zeile eins\nZeile zwei")
        self.assertIn('keystroke "Zeile eins"', script)
        self.assertIn("keystroke return", script)
        self.assertIn('keystroke "Zeile zwei"', script)

    def test_no_raw_newline_inside_any_literal(self):
        script = bridge._keystroke_script("a\nb\r\nc\rd")
        for line in script.splitlines():
            if line.startswith('keystroke "'):
                self.assertTrue(line.endswith('"'),
                                f"Literal ueber Zeilengrenze: {line!r}")

    def test_quotes_and_backslashes_stay_escaped(self):
        script = bridge._keystroke_script('sag "hallo" \\ tschuess')
        self.assertIn('\\"hallo\\"', script)
        self.assertIn("\\\\", script)

    def test_trailing_newline_presses_return_last(self):
        script = bridge._keystroke_script("fertig\n")
        self.assertTrue(script.rstrip().endswith("keystroke return\nend tell"),
                        script)

    def test_type_text_uses_this_builder(self):
        src = Path(bridge.__file__).read_text(encoding="utf-8")
        self.assertIn("_keystroke_script(text)", src,
                      "type_text baut sein AppleScript wieder von Hand")


if __name__ == "__main__":
    unittest.main()
