"""Im Sprachmodus darf kein „n" mitten im Satz vorgelesen werden.

Nutzerbericht vom 21.08.2026, mit Bildschirmfoto des Live-Gespraechs:

    „Hier sind ein paar Treffer zu Inside AI auf YouTube: n n1. InsideAI** –
     Beschreibt sich als Made by humans, for humans"

Zwei Fehler in einem Satz: das ``n n`` ist ein zerbrochener Zeilenumbruch, das
``**`` uebrig gebliebenes Markdown.

Der Hergang beim Umbruch — und er ist lehrreich, weil die Reparatur schon
einmal da war:

* ``_clean_text`` wandelt literale ``\\n``-Folgen in echte Umbrueche zurueck.
  Der Kommentar dort beschreibt genau dieses Symptom.
* ``send_tool_result`` verpackt den Text danach ein ZWEITES Mal mit
  ``json.dumps`` — und macht aus dem echten Umbruch wieder die zwei sichtbaren
  Zeichen ``\\`` und ``n``.
* Nova reicht diese Zeichenkette woertlich ans Modell. Den Backslash kann es
  nicht sprechen, also bleibt das ``n`` uebrig.

Die Reparatur wurde also von der naechsten Zeile wieder zunichte gemacht. Fuer
gesprochenen Text traegt ein Umbruch ohnehin keine Bedeutung.
"""

import json
import unittest

from app.services.voice_providers.realtime_nova_sonic import _clean_text, _sprechbar


#: Woertlich aus dem gemeldeten Fall, ohne den Kanalnamen zu erfinden.
ECHTER_FALL = (
    "Web-Ergebnisse zu „Inside AI“:\n\n"
    "1. **InsideAI** – Beschreibt sich als „Made by humans, for humans“\n"
    "2. **Inside AI** mit dem Untertitel „Exploring the future of intelligence“"
)


class NoStrayLetterSurvivesTheEncodingTests(unittest.TestCase):
    def test_the_reported_sentence_no_longer_contains_a_stray_n(self):
        verpackt = json.dumps({"result": _sprechbar(ECHTER_FALL)})
        self.assertNotIn("\\n", verpackt)

    def test_that_was_broken_before_the_fix(self):
        """Die alte Fassung — zum Vergleich, damit klar ist, was sich geaendert
        hat: mit `_clean_text` allein ueberlebt der Umbruch die Kodierung nicht."""
        verpackt_alt = json.dumps({"result": _clean_text(ECHTER_FALL)})
        self.assertIn("\\n", verpackt_alt)

    def test_a_paragraph_becomes_a_spoken_pause(self):
        self.assertEqual(_sprechbar("Erster Absatz\n\nZweiter Absatz"),
                         "Erster Absatz. Zweiter Absatz")

    def test_a_single_line_break_becomes_a_space(self):
        self.assertEqual(_sprechbar("Zeile eins\nZeile zwei"), "Zeile eins Zeile zwei")

    def test_no_double_punctuation_after_a_colon(self):
        """Sonst hoert man „Ergebnisse:. Eins" — der Doppelpunkt steht ja schon."""
        self.assertIn("Inside AI“: 1.", _sprechbar(ECHTER_FALL))

    def test_literal_escapes_from_elsewhere_are_still_repaired(self):
        """Kommt Text als JSON-Zeichenkette an (Gedaechtnis, Datei-Auszug),
        steht dort BACKSLASH+n. Das war die urspruengliche Reparatur und muss
        erhalten bleiben."""
        self.assertEqual(_sprechbar("Punkt eins\\nPunkt zwei"), "Punkt eins Punkt zwei")


class MarkdownIsNotReadAloudTests(unittest.TestCase):
    """Im selben Bildschirmfoto: „InsideAI** –" — die Sternchen wurden
    mitgesprochen."""

    def test_bold_markers_are_gone(self):
        self.assertNotIn("**", _sprechbar(ECHTER_FALL))

    def test_the_word_itself_stays(self):
        self.assertIn("InsideAI", _sprechbar(ECHTER_FALL))

    def test_a_link_keeps_its_text_and_drops_the_target(self):
        gesprochen = _sprechbar("Siehe [die Doku](https://example.invalid/x) dazu")
        self.assertIn("Siehe die Doku dazu", gesprochen)
        self.assertNotIn("example.invalid", gesprochen)

    def test_headings_lose_their_hashes(self):
        self.assertEqual(_sprechbar("## Ergebnis"), "Ergebnis")

    def test_code_ticks_are_gone(self):
        self.assertEqual(_sprechbar("Nutze `list_projects` dafuer"),
                         "Nutze list_projects dafuer")


class OnlyTheDoublyEncodedPathIsChangedTests(unittest.TestCase):
    """`inject_user_text` sendet direkt — dort ueberleben echte Umbrueche, dort
    gab es das Problem nie. Der Eingriff bleibt auf den Weg beschraenkt, der
    tatsaechlich ein zweites Mal kodiert."""

    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[1]
              / "app/services/voice_providers/realtime_nova_sonic.py").read_text()

    def test_the_tool_result_uses_the_speakable_form(self):
        block = self.QUELLE.split("async def send_tool_result", 1)[1][:1400]
        self.assertIn("_sprechbar(result)", block)

    def test_the_clean_text_repair_is_still_there(self):
        """Sie ist die Grundlage — `_sprechbar` baut darauf auf."""
        self.assertIn('s.replace("\\\\r\\\\n", "\\n").replace("\\\\n", "\\n")', self.QUELLE)


if __name__ == "__main__":
    unittest.main()
