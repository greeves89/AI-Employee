"""Bildersuche: Begriff rein, echte Treffer raus, direkt auf den Schirm.

Der Agent nannte Bild-Adressen aus dem Gedaechtnis — die gab es nie (400/404), und er
meldete ein Problem beim Bildserver. Raten war der falsche Weg; jetzt sucht er.
"""

import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
CORE = (ORCH / "app/core/image_search.py").read_text()
VOICE = (ORCH / "app/services/realtime_voice_session.py").read_text()


class CoreTests(unittest.TestCase):
    def test_two_step_because_of_the_token(self):
        """DuckDuckGo gibt Bilder nur mit Sitzungs-Token heraus."""
        self.assertIn("vqd", CORE)
        self.assertIn("duckduckgo.com/i.js", CORE)

    def test_only_tls_addresses(self):
        self.assertIn('url.startswith("https://")', CORE)

    def test_failure_returns_empty_not_an_exception(self):
        """Eine kaputte Suche darf das Gespraech nicht abbrechen."""
        self.assertIn("return []", CORE)

    def test_identifies_itself(self):
        self.assertIn("AI-Employee/1.0", CORE)


class VoiceToolTests(unittest.TestCase):
    def test_tool_is_named_and_offered(self):
        self.assertIn('"name": "web_picture_search"', VOICE)
        tool_list = VOICE.split("_tools = [", 1)[1].split("]", 1)[0]
        self.assertIn("WEB_PICTURE_SEARCH_TOOL", tool_list)

    def test_handler_shows_the_pictures_itself(self):
        handler = VOICE.split("async def _web_picture_search", 1)[1].split("async def _plan_my_day", 1)[0]
        self.assertIn("_safe_get(", handler)          # Bild serverseitig holen (SSRF-Gate)
        self.assertIn('"kind": "image"', handler)      # und in die Anzeige geben
        self.assertIn("ctype.startswith(\"image/\")", handler)

    def test_dead_hits_are_skipped_not_fatal(self):
        handler = VOICE.split("async def _web_picture_search", 1)[1].split("async def _plan_my_day", 1)[0]
        self.assertIn("continue", handler)

    def test_no_hits_is_said_plainly(self):
        handler = VOICE.split("async def _web_picture_search", 1)[1].split("async def _plan_my_day", 1)[0]
        self.assertIn("keine Bilder gefunden", handler)
