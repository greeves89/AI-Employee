"""Literale Escape-Folgen im Sprachtext.

Der Agent fasste seine Arbeit zusammen, und im Transkript stand ueberall ein einsames
"n" mitten im Satz: „n1. Backlog-Priorisierung n - OAuth Re-Auth 500". Ursache: der Text
erreichte die Engine mit BACKSLASH+n statt einem echten Umbruch (JSON-Zeichenkette aus
einem Werkzeug-Ergebnis). Sprechen kann man einen Backslash nicht — er faellt weg, das
"n" bleibt stehen.
"""

import unittest

from app.services.voice_providers.realtime_nova_sonic import _clean_text


class EscapeTests(unittest.TestCase):
    def test_literal_newline_becomes_a_real_one(self):
        self.assertEqual(_clean_text("Punkt eins:\\n1. Backlog"), "Punkt eins:\n1. Backlog")

    def test_the_reported_case(self):
        out = _clean_text("Zusammenfassung:\\n1. Backlog\\n - OAuth Re-Auth")
        self.assertNotIn("\\n", out)
        self.assertNotIn(" n ", out.replace("\n", " "))   # kein einsames n mehr
        self.assertEqual(out.count("\n"), 2)

    def test_windows_and_tabs_too(self):
        self.assertEqual(_clean_text("a\\r\\nb"), "a\nb")
        self.assertEqual(_clean_text("a\\tb"), "a\tb")

    def test_real_newlines_stay_untouched(self):
        self.assertEqual(_clean_text("a\nb"), "a\nb")

    def test_control_chars_are_still_stripped(self):
        """Der alte Zweck bleibt: Steuerzeichen aus PDF-Auszuegen zerlegen den Stream."""
        self.assertEqual(_clean_text("a\x00b\x07c"), "abc")

    def test_broken_surrogates_still_survive_cleaning(self):
        self.assertIsInstance(_clean_text("gut \ud800 text"), str)


if __name__ == "__main__":
    unittest.main()
