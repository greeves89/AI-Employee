"""``truncate_preserving_words``: Kuerzen ohne mitten im Wort abzubrechen.

Anlass: die Fertigmeldung eines delegierten Auftrags kuerzte mit einem blossen
``text[:n]``. Ein Sub-Agent beschreibt vor der eigentlichen Antwort erst
pflichtgemaess seine Vorabchecks — dieser Vorspann war oft laenger als das
Limit, und die Antwort selbst ("Hallo Welt") fiel komplett aus dem sichtbaren
Text. Siehe ``test_delegation_result_not_cut_mid_word.py`` fuer die Stellen,
die diese Funktion tatsaechlich benutzen.
"""

import unittest

from app.core.text_preview import truncate_preserving_words


class TruncatePreservingWordsTests(unittest.TestCase):
    def test_short_text_is_returned_unchanged(self):
        self.assertEqual(truncate_preserving_words("Hallo Welt", 800), "Hallo Welt")

    def test_text_exactly_at_the_limit_is_unchanged(self):
        text = "a" * 10
        self.assertEqual(truncate_preserving_words(text, 10), text)

    def test_cuts_at_the_last_word_boundary_before_the_limit(self):
        # "Hallo Wel" waere ein Wort mittendrin abgeschnitten — die Funktion
        # geht stattdessen bis vor "Welt" zurueck.
        result = truncate_preserving_words("Hallo Welt und weitere Worte", 12)
        self.assertEqual(result, "Hallo Welt […]")
        self.assertFalse(result.startswith("Hallo Wel "))

    def test_never_slices_a_word_in_half(self):
        text = "Vorabcheck abgeschlossen. Antwort folgt: Hallo Welt."
        for limit in range(5, len(text)):
            with self.subTest(limit=limit):
                result = truncate_preserving_words(text, limit)
                # Entweder unveraendert (Text passt), oder endet auf dem
                # Ellipsen-Marker — nie mitten in einem Originalwort.
                if result != text:
                    self.assertTrue(result.endswith(" […]"), result)

    def test_single_word_longer_than_limit_still_gets_a_marker(self):
        # Kein Leerzeichen vor dem Limit vorhanden — harter Fallback aufs
        # Limit selbst, aber weiterhin mit erkennbarem Abbruch-Marker.
        result = truncate_preserving_words("A" * 50, 10)
        self.assertEqual(result, "A" * 10 + " […]")


if __name__ == "__main__":
    unittest.main()
