"""Die Stimme wird VORGELESEN — Markdown gehoert nicht hinein.

Nutzerbericht vom 21.08.2026, zwei Bildschirmfotos:

1. „Hier sind die wichtigsten Punkte zur Performance: n n-   Echtzeit-Faehigkeit**:
   Coqui TTS ist …" — das Modell hatte ``\\n\\n- **Echtzeit-Faehigkeit**:``
   formatiert, und die Sprachsynthese liest die Markup-Zeichen mit.
2. „Hier sind die Hauptoptionen und was du beachten solltest:" — und dann nichts.
   Die angekuendigte Liste kam gar nicht.

**Wichtig fuer die Nachwelt: das ist KEIN Escape-Fehler in unserem Code.** Der
gespeicherte Text enthaelt weder Backslash noch Zeilenumbruch — das ``n`` steht
schon so in dem, was die Engine zurueckgibt. Zwei Vermutungen wurden vorher
geprueft und verworfen:

* die Textsaeuberung (``_clean_text``) entfernt keine Backslashes
* Erinnerungen und Gespraechsverlauf enthalten keine literalen ``\\n``-Folgen

Es ist eine Formatierungsgewohnheit des Modells: fuers Auge geschrieben, obwohl
es fuers Ohr ist. Bei einem Sprache-zu-Sprache-Modell gibt es dagegen KEINEN
mechanischen Hebel — gesprochen ist gesprochen, die Audioausgabe laesst sich
nicht nachtraeglich saeubern. Bleibt die Anweisung, und die gehoert nach ganz
vorn.
"""

import unittest

from app.services.realtime_voice_session import _system_prompt


PROMPT = _system_prompt("Testagent", "Testrolle", "de")


class TheRuleIsImpossibleToMissTests(unittest.TestCase):
    def test_it_stands_at_the_very_top(self):
        """Weiter unten liest es kein Modell mit begrenztem Blick auf einen
        langen Text — dieselbe Lehre wie bei den Master-Regeln."""
        self.assertIn("DU WIRST VORGELESEN", PROMPT)
        self.assertLess(PROMPT.index("DU WIRST VORGELESEN"), 700)

    def test_it_comes_before_the_other_house_rules(self):
        self.assertLess(PROMPT.index("DU WIRST VORGELESEN"),
                        PROMPT.index("NICHT LAUT DENKEN"))

    def test_it_names_the_forbidden_characters(self):
        for zeichen in ("Sternchen", "Bindestrich-Listen", "Zeilenumbrüche", "Backticks"):
            with self.subTest(zeichen=zeichen):
                self.assertIn(zeichen, PROMPT)

    def test_it_says_why_and_not_just_what(self):
        """„Verboten" allein haelt schlecht; das Modell soll den Grund kennen."""
        self.assertIn("LAUT MITGESPROCHEN", PROMPT)

    def test_it_offers_the_replacement(self):
        """Eine Verbotsregel ohne Alternative laesst das Modell ratlos."""
        self.assertIn("erstens", PROMPT)
        self.assertIn("zweitens", PROMPT)

    def test_the_dangling_colon_is_covered(self):
        """Das zweite gemeldete Symptom: Ankuendigung ohne Inhalt."""
        self.assertIn("Doppelpunkt", PROMPT)
        self.assertIn("schlimmer als keine", PROMPT)


class TheTranscriptStaysHonestTests(unittest.TestCase):
    """Bewusst NICHT gesaeubert: das Transkript ist die Aufzeichnung dessen,
    was gesprochen wurde. Wuerden wir dort die Markup-Reste entfernen, saehe es
    sauber aus, waehrend der Nutzer weiter „Sternchen" hoert — ein kosmetischer
    Fix, der die Diagnose beim naechsten Mal unmoeglich macht."""

    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[1]
              / "app/services/realtime_voice_session.py").read_text()

    def test_the_assistant_text_is_stored_as_received(self):
        block = self.QUELLE.split('else:  # ASSISTANT / other', 1)
        self.assertEqual(len(block), 2, "Zweig fuer Assistententext nicht gefunden")
        rumpf = block[1][:300]
        self.assertIn('_persist_turn("assistant", text)', rumpf)
        self.assertNotIn("_sprechbar", rumpf)


if __name__ == "__main__":
    unittest.main()
