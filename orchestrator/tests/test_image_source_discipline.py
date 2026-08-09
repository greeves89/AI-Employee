"""Bild-Adressen werden gesucht, nicht geraten.

Der Agent nannte im Gespraech Wikimedia-Links, die es nie gab (400/404) — und meldete
dann ein Problem beim Bildserver. Die Adressen waren erfunden. Also: erst suchen, dann
zeigen; und wenn es doch schiefgeht, sagt die Fehlermeldung, was zu tun ist.
"""

import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/services/realtime_voice_session.py").read_text()


class ImageSourceTests(unittest.TestCase):
    def test_tool_forbids_guessing(self):
        self.assertIn("NEVER invent or assemble an image URL", SRC)

    def test_tool_names_the_two_legitimate_ways(self):
        self.assertIn("`web_search`", SRC)
        self.assertIn("api.php?action=query", SRC)

    def test_failure_message_is_actionable(self):
        """„Bild konnte nicht geladen werden" sagt dem Modell nichts — es soll suchen."""
        self.assertIn("Such sie mit `web_search`", SRC)
        self.assertNotIn('return "Bild konnte nicht geladen werden."', SRC)

    def test_html_page_is_distinguished_from_an_image(self):
        self.assertIn("kein Bild, sondern eine Seite", SRC)
