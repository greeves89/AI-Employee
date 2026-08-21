"""Klicks auf dem richtigen Bildschirm — und der echte Grund, wenn etwas scheitert.

Zwei Meldungen vom 21.08.2026, kurz nacheinander:

1. „das ging tatsaechlich voll daneben du hast gerade einfach nur meinen coding
   agent eingestellt" — der Agent klickte auf Bildschirm 2 und traf etwas
   anderes. In der Aktivitaetsspalte stand ``display: 2.0, x: 123.0, y: 456.0``:
   das sind die BEISPIELWERTE aus der Werkzeugbeschreibung. Er hat die
   Koordinaten geraten.
2. „wieso kann der den nicht auswerten? -.-" — die Stimme sagte immer nur „die
   Auswertung kam nicht zurueck". Der echte Grund lag im Klartext vor:
   ``[Fehler: You've hit your limit · resets 3:10pm]`` — das Kontingent des
   Agenten war aufgebraucht. Die Meldung wurde weggeworfen, und der Nutzer
   suchte eine halbe Stunde bei den Bildern.

Dazu eine dritte, eigene Luecke: der Versatz fuer den zweiten Monitor hing
allein am ZULETZT aufgenommenen Screenshot. Wer „klick auf Bildschirm zwei"
sagt, ohne dass unmittelbar davor ein Screenshot genau dieses Bildschirms lief,
klickte mit dem Versatz des falschen Monitors.
"""

import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
BRIDGE = (WURZEL / "computer-use-bridge/bridge.py").read_text()
VOICE = (WURZEL / "orchestrator/app/services/realtime_voice_session.py").read_text()


class AClickKnowsWhichScreenItMeansTests(unittest.TestCase):
    def test_a_named_display_sets_the_offset(self):
        self.assertIn("def _display_offset(", BRIDGE)
        block = BRIDGE.split("def _display_offset(", 1)[1][:900]
        self.assertIn("list_displays()", block)

    def test_it_beats_the_last_screenshot(self):
        """Ausdruecklich genannt schlaegt zuletzt gesehen — sonst haengt der
        Klick an einem Zustand, den der Nutzer nicht sieht."""
        block = BRIDGE.split("def _to_click_space(", 1)[1][:500]
        self.assertIn("self._display_offset(display) or self._coord_offset", block)

    def test_every_pointer_action_passes_it_through(self):
        """Klicken, Bewegen, Scrollen und Ziehen — eine ausgelassene Stelle
        waere genau der Fall, der spaeter danebengeht."""
        self.assertEqual(BRIDGE.count('self._to_click_space(params["x"], params["y"], params.get("display"))'), 3)
        self.assertIn('params["x1"], params["y1"], params.get("display")', BRIDGE)

    def test_without_a_display_nothing_changes(self):
        """Ein Aufrufer ohne Bildschirmangabe muss sich verhalten wie bisher."""
        block = BRIDGE.split("def _display_offset(", 1)[1][:900]
        self.assertIn("if not display:", block)
        self.assertIn("return None", block)

    def test_the_voice_forwards_the_display_on_a_click(self):
        self.assertIn('params["display"] = int(display)', VOICE)


class TheModelIsToldNotToGuessTests(unittest.TestCase):
    """Die geratenen 123/456 stammten aus der Beispielzeile der
    Werkzeugbeschreibung — das Modell hat sie schlicht uebernommen."""

    def test_guessing_coordinates_is_forbidden_in_plain_words(self):
        self.assertIn("KOORDINATEN NIEMALS RATEN", VOICE)

    def test_it_says_where_valid_coordinates_come_from(self):
        """Ein Verbot ohne Bezugsquelle laesst das Modell ratlos — und dann
        raet es wieder."""
        block = VOICE.split("KOORDINATEN NIEMALS RATEN", 1)[1][:500]
        self.assertIn("`find`", block)
        self.assertIn("Screenshot", block)

    def test_the_two_screen_order_is_spelled_out(self):
        self.assertIn("MEHRERE BILDSCHIRME:", VOICE)
        block = VOICE.split("MEHRERE BILDSCHIRME:", 1)[1][:300]
        self.assertIn("display=N", block)


class AFailedAnalysisNamesItsReasonTests(unittest.TestCase):
    def test_the_reason_is_extracted_not_discarded(self):
        block = VOICE.split("async def _analyse_screenshot_bg", 1)[1][:2600]
        self.assertIn("grund", block)
        self.assertIn('answer[8:]', block)

    def test_the_model_is_told_to_pass_it_on(self):
        block = VOICE.split("async def _analyse_screenshot_bg", 1)[1][:2600]
        self.assertIn("Sag ihm diesen Grund", block)

    def test_it_no_longer_asks_the_user_what_he_sees_when_the_reason_is_known(self):
        """Genau das war die Reibung: der Nutzer wurde zurueckgefragt, obwohl
        die Ursache im Klartext vorlag."""
        block = VOICE.split("async def _analyse_screenshot_bg", 1)[1][:2600]
        self.assertIn("frage nicht, was er sieht", block)

    def test_without_a_reason_the_old_wording_stays(self):
        """Kam wirklich nichts zurueck, bleibt die Rueckfrage richtig."""
        block = VOICE.split("async def _analyse_screenshot_bg", 1)[1][:2600]
        self.assertIn("ohne Begruendung", block)


if __name__ == "__main__":
    unittest.main()
