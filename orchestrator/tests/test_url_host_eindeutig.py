"""Freigabelisten vergleichen den Host — aber welchen?

Python (``urlparse``) und Browser (WHATWG, Chromium) lesen manche Adressen
verschieden. ``https://evil.com\\.example.com/`` ist für Python der Host
``evil.com\\.example.com`` (endet auf ``.example.com`` → erlaubt), für Chromium
aber ``evil.com``. So kam eine fremde Seite durch jede Freigabeliste. Solche
mehrdeutigen Adressen gelten jetzt überall als „kein Host“ und werden abgelehnt.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.api.computer_use import _url_in_scope
from app.api.url_allowlist import url_matches_pattern
from app.core import url_guard
from app.core.url_guard import eindeutiger_host

# Jeweils: Python sähe einen erlaubten Host, der Browser ruft evil.com auf.
MEHRDEUTIG = (
    "https://evil.com\\.example.com/",
    "https://evil.com\\@example.com/",
    "https://evil.com\\example.com/",
    "https://evil.com%2f.example.com/",
    "https://evil.com\t.example.com/",
    "https://evil.com .example.com/",
)


class EindeutigerHost(unittest.TestCase):
    def test_normale_adressen(self):
        self.assertEqual(eindeutiger_host("https://A.Example.com/x?y=1"), "a.example.com")
        self.assertEqual(eindeutiger_host("https://user@example.com:8443/"), "example.com")

    def test_mehrdeutige_adressen_haben_keinen_host(self):
        for url in MEHRDEUTIG:
            with self.subTest(url=url):
                self.assertIsNone(eindeutiger_host(url))

    def test_backslash_im_pfad_bleibt_erlaubt_nur_nicht_im_host(self):
        # Hinter dem ersten "/" nach dem Host liest auch der Browser einen Pfad.
        self.assertEqual(eindeutiger_host("https://example.com/a\\b"), "example.com")


class Freigabelisten(unittest.IsolatedAsyncioTestCase):
    def test_bridge_sitzung(self):
        for url in MEHRDEUTIG:
            with self.subTest(url=url):
                self.assertFalse(_url_in_scope(url, ["example.com"]))
        self.assertTrue(_url_in_scope("https://a.example.com/x", ["example.com"]))

    def test_url_freigabeliste(self):
        for url in MEHRDEUTIG:
            with self.subTest(url=url):
                self.assertFalse(url_matches_pattern(url, "*.example.com"))
                self.assertFalse(url_matches_pattern(url, "example.com"))
        self.assertTrue(url_matches_pattern("https://a.example.com/", "*.example.com"))

    async def test_rueckruf(self):
        eintrag = MagicMock(url_pattern="example.com")
        ergebnis = MagicMock()
        ergebnis.scalars.return_value.all.return_value = [eintrag]
        db = MagicMock(execute=AsyncMock(return_value=ergebnis))
        orig = url_guard.check_outbound_url
        url_guard.check_outbound_url = lambda url: (True, "")
        self.addCleanup(setattr, url_guard, "check_outbound_url", orig)
        for url in MEHRDEUTIG:
            with self.subTest(url=url):
                self.assertFalse(await url_guard.is_allowed_callback(url, db=db, agent_id="a1"))
        self.assertTrue(await url_guard.is_allowed_callback("https://a.example.com/", db=db, agent_id="a1"))


if __name__ == "__main__":
    unittest.main()
