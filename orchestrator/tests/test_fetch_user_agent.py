"""Abrufe nach draussen nennen ihren Namen.

Im Sprachmodus liessen sich keine Bilder anzeigen: „Bild konnte nicht geladen werden".
Ursache war nicht der Bildserver, sondern unsere Anfrage — sie kam OHNE User-Agent, und
Wikimedia (wie viele andere) antwortet darauf mit einem text/plain-Hinweis auf ihre
Robot-Policy statt mit dem Bild. Der Inhaltstyp passte dann nicht, und der Agent meldete
ein technisches Problem beim Bildserver.
"""

import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/services/realtime_voice_session.py").read_text()
FETCH = SRC.split("async def _safe_get", 1)[1].split("\nasync def ", 1)[0]


class UserAgentTests(unittest.TestCase):
    def test_requests_identify_themselves(self):
        self.assertIn('"User-Agent"', FETCH)
        self.assertIn("AI-Employee/", FETCH)

    def test_contact_url_is_included(self):
        """Eine Kontaktadresse ist das, was die Policies verlangen — anonym reicht nicht."""
        self.assertIn("github.com/greeves89/AI-Employee", FETCH)

    def test_accept_header_asks_for_images(self):
        self.assertIn('"Accept"', FETCH)
        self.assertIn("image/*", FETCH)

    def test_host_pinning_stays_intact(self):
        """Der User-Agent darf die SSRF-Absicherung nicht verdraengen."""
        self.assertIn('"Host": host', FETCH)


if __name__ == "__main__":
    unittest.main()
