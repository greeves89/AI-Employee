"""Gesetze-Crawler-Schalter (#890): Entscheidung ueber Start/Nicht-Start.

Ausdrueckliche Einstellung gewinnt immer; ohne Einstellung laeuft der Crawler
nur, wenn auf der Anlage schon Gesetzesdaten liegen (Bestandsschutz).
"""

import asyncio
import unittest

from app.services.gesetz_crawler import GesetzCrawlerService, crawler_aktiv


class CrawlerAktivTest(unittest.TestCase):
    def test_true_erzwingt_an_auch_ohne_bestand(self):
        for wert in ("true", "TRUE", " 1 ", "yes", "on"):
            self.assertTrue(crawler_aktiv(wert, False)[0], wert)

    def test_false_erzwingt_aus_auch_mit_bestand(self):
        for wert in ("false", "FALSE", "0", "no", "off"):
            self.assertFalse(crawler_aktiv(wert, True)[0], wert)

    def test_nicht_gesetzt_mit_bestand_laeuft(self):
        for wert in (None, "", "  "):
            self.assertTrue(crawler_aktiv(wert, True)[0])

    def test_nicht_gesetzt_ohne_bestand_aus(self):
        for wert in (None, "", "  "):
            self.assertFalse(crawler_aktiv(wert, False)[0])

    def test_unbekannter_wert_wie_nicht_gesetzt(self):
        self.assertTrue(crawler_aktiv("vielleicht", True)[0])
        self.assertFalse(crawler_aktiv("vielleicht", False)[0])

    def test_grund_ist_immer_ein_text(self):
        for wert in ("true", "false", None):
            for bestand in (True, False):
                self.assertTrue(crawler_aktiv(wert, bestand)[1])


class AusgeschalteterCrawlerTest(unittest.TestCase):
    def test_run_kehrt_sofort_zurueck_und_crawlt_nicht(self):
        c = GesetzCrawlerService(aktiv=False)
        aufrufe = []

        async def _nie(*a, **k):
            aufrufe.append(1)

        c.crawl = _nie
        c.crawl_eu = _nie
        asyncio.run(asyncio.wait_for(c.run(), timeout=2))
        self.assertEqual(aufrufe, [])
        self.assertFalse(c.aktiv)
        self.assertEqual(c.law_count, 0)
        self.assertIsNone(c.last_crawled_at)


if __name__ == "__main__":
    unittest.main()
