"""Ein kurzer Netzaussetzer darf keinen Hintergrund-Lauf kosten und keine Frische vortaeuschen.

Gemessener Anlass (#746): am 13.10. fiel die Namensaufloesung der Anlage 34 Sekunden
aus (04:00:57-04:01:31). Das hat ZWEI vollstaendige Laeufe gekostet — der
Skill-Crawler scheiterte an allen Quellen, der Gesetz-Crawler an EU und DE. Beide
machten je Ziel genau einen Versuch.

Schlimmer als der verlorene Lauf war die Spur, die er hinterliess: der
Skill-Crawler schrieb danach einen LEEREN Katalog mit frischem ``crawled_at`` in
den Zwischenspeicher (sieben Tage haltbar, naechster Lauf erst in einer Woche),
der Gesetz-Crawler setzte seinen Frischestempel ebenfalls weiter. ``/api/compliance``
meldete "gerade gecrawlt, 0 Normen" — nicht unterscheidbar von einem leeren
Bestand. Genau diese Klasse ist der Gegenstand von #746 (infra/observability).

Geprueft wird deshalb beides: dass wiederholt wird, UND dass ein trotzdem
gescheiterter Lauf die alten Angaben stehen laesst statt sie zu ueberschreiben.
"""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.core.verbindungswiederholung import PAUSEN_HINTERGRUND_S
from app.services.gesetz_crawler import GesetzCrawlerService, _AusfallZaehler


def _lauf(coro):
    return asyncio.run(coro)


class LeiterTests(unittest.TestCase):
    """Die Pausenleiter muss zum gemessenen Fenster passen, nicht zum Push-Pfad."""

    def test_hintergrundleiter_deckt_das_gemessene_fenster_von_34_sekunden(self):
        # Die Push-Leiter aus #947 deckt 2,0 s. Haette man sie hier
        # wiederverwendet, waere der Fix wirkungslos geblieben — das ist der
        # Grund fuer eine eigene Konstante, und deshalb steht die Zahl im Test.
        self.assertGreaterEqual(
            sum(PAUSEN_HINTERGRUND_S), 34.0,
            "Hintergrund-Leiter muss das gemessene 34-s-Fenster vom 13.10. abdecken",
        )


class GesetzFrischeTests(unittest.TestCase):
    """Frischestempel und Normenzahl duerfen einen Netzaussetzer nicht ueberschreiben."""

    def _zaehler(self, *, verbindung: int, inhalt: int = 0) -> _AusfallZaehler:
        z = _AusfallZaehler("Test")
        for i in range(verbindung):
            z.melde(f"netz{i}", httpx.ConnectError("[Errno -3] Temporary failure in name resolution"))
        for i in range(inhalt):
            z.melde(f"inhalt{i}", ValueError("kaputtes XML"))
        return z

    def test_zaehler_trennt_verbindung_von_inhalt(self):
        z = self._zaehler(verbindung=2, inhalt=3)
        self.assertEqual(z.anzahl, 5)
        self.assertEqual(z.verbindungsausfaelle, 2)

    def test_netzaussetzer_erkannt_nur_wenn_nichts_geschrieben_wurde(self):
        netz = self._zaehler(verbindung=4)
        self.assertTrue(GesetzCrawlerService._netzaussetzer(netz, 0))
        # Ein Lauf, der trotz Aussetzern etwas geschrieben hat, ist ein normaler
        # Lauf: der Stempel darf weiterruecken.
        self.assertFalse(GesetzCrawlerService._netzaussetzer(netz, 1))
        # 0 geschrieben, aber die Fehler lagen am Inhalt -> echter Befund, kein Netz.
        self.assertFalse(GesetzCrawlerService._netzaussetzer(self._zaehler(verbindung=0, inhalt=4), 0))

    def test_eu_lauf_am_netz_gescheitert_laesst_stempel_und_zahl_stehen(self):
        c = GesetzCrawlerService()
        c.eu_last_crawled_at = "2026-10-12T04:00:00+00:00"
        c.eu_law_count = 7

        class Kaputt:
            async def get(self, *a, **k):
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        with patch("app.services.gesetz_crawler.httpx.AsyncClient", lambda *a, **k: Kaputt()), \
             patch("app.services.gesetz_crawler._vorhandene_teile", lambda *a: {}), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)):
            geschrieben = _lauf(c.crawl_eu())

        self.assertEqual(geschrieben, 0)
        self.assertEqual(c.eu_last_crawled_at, "2026-10-12T04:00:00+00:00",
                         "Frischestempel wurde trotz Netzaussetzer weitergerueckt")
        self.assertEqual(c.eu_law_count, 7, "Normenzahl wurde auf 0 gesetzt")


    def test_de_lauf_am_netz_gescheitert_laesst_stempel_und_zahl_stehen(self):
        # Eigener Test fuer den DE-Pfad: er hat seine eigene Stempel-Zuweisung.
        # Ohne diesen Test blieb beim Zurueckdrehen der DE-Wache alles gruen
        # (ueberlebender Mutant M9 der ersten Batterie).
        c = GesetzCrawlerService()
        c.last_crawled_at = "2026-10-12T04:00:00+00:00"
        c.law_count = 6123

        class Leer:
            async def get(self, *a, **k):
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        async def toc(_selbst, _client):
            return [("Grundgesetz", "gg")]

        with patch("app.services.gesetz_crawler.httpx.AsyncClient", lambda *a, **k: Leer()), \
             patch("app.services.gesetz_crawler._vorhandene_teile", lambda *a: {}), \
             patch.object(GesetzCrawlerService, "_fetch_toc", toc), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)):
            geschrieben = _lauf(c.crawl())

        self.assertEqual(geschrieben, 0)
        self.assertEqual(c.last_crawled_at, "2026-10-12T04:00:00+00:00",
                         "DE-Frischestempel wurde trotz Netzaussetzer weitergerueckt")
        self.assertEqual(c.law_count, 6123, "DE-Normenzahl wurde auf 0 gesetzt")


class WiederholungVerdrahtetTests(unittest.TestCase):
    """Nicht nur die Leiter, sondern die AUFRUFSTELLEN muessen wiederholen.

    Ohne diese Tests bliebe die Verdrahtung ungedeckt: die Frische-Tests oben
    werden auch gruen, wenn gar nicht wiederholt wird (sie messen den Fall, in
    dem jeder Versuch scheitert).
    """

    class _ZaehlClient:
        """Scheitert die ersten ``fehler`` Male an der Verbindung, dann Erfolg."""

        def __init__(self, fehler: int, antwort):
            self.fehler = fehler
            self.antwort = antwort
            self.aufrufe = 0

        async def get(self, *a, **k):
            self.aufrufe += 1
            if self.aufrufe <= self.fehler:
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            return self.antwort

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    def test_eu_norm_wird_nach_zwei_verbindungsfehlern_doch_geholt(self):
        class Antwort:
            status_code = 200
            content = b"<html></html>"

        client = self._ZaehlClient(2, Antwort())
        c = GesetzCrawlerService()
        c.eu_last_crawled_at = None
        geschrieben_an = []

        async def schreiben(*a, **k):
            geschrieben_an.append(a)

        with patch("app.services.gesetz_crawler.httpx.AsyncClient", lambda *a, **k: client), \
             patch("app.services.gesetz_crawler._vorhandene_teile", lambda *a: {}), \
             patch("app.services.gesetz_crawler.EU_CURATED_LAWS", [("32024R1689", "EU AI Act")]), \
             patch("app.services.gesetz_crawler.parse_eu_regulation_xhtml",
                   lambda *a, **k: {"titel": "EU AI Act", "teile": []}), \
             patch("app.services.gesetz_crawler._gesetz_schreiben", schreiben), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0, 0.0, 0.0)):
            written = _lauf(c.crawl_eu())

        self.assertEqual(client.aufrufe, 3, "es wurde nicht bis zum Erfolg wiederholt")
        self.assertEqual(written, 1, "die Norm wurde trotz geglueckter Wiederholung nicht geschrieben")
        self.assertEqual(len(geschrieben_an), 1)
        self.assertIsNotNone(c.eu_last_crawled_at, "geglueckter Lauf muss den Stempel setzen")

    def test_skill_baum_wird_nach_einem_verbindungsfehler_doch_geholt(self):
        from app.services.skill_crawler import SkillCrawlerService

        class Antwort:
            status_code = 200
            def json(self): return {"tree": []}

        client = self._ZaehlClient(1, Antwort())
        c = SkillCrawlerService(None)
        with patch("app.services.skill_crawler.PAUSEN_HINTERGRUND_S", (0.0, 0.0, 0.0)):
            ergebnis = _lauf(c._crawl_github(client, "a/b"))

        self.assertEqual(ergebnis, [])
        self.assertEqual(client.aufrufe, 2, "der Baum-Abruf wurde nicht wiederholt")


class SkillKatalogTests(unittest.TestCase):
    """Der Zwischenspeicher darf nach einem Netzaussetzer nicht leer ueberschrieben werden."""

    def _crawler(self):
        from app.services.skill_crawler import SkillCrawlerService

        class Redis:
            def __init__(self): self.gesetzt = []
            class _C:
                def __init__(self, aussen): self.aussen = aussen
                async def set(self, key, wert, ex=None): self.aussen.gesetzt.append((key, wert))
                async def get(self, key): return None
            @property
            def client(self): return Redis._C(self)

        r = Redis()
        return SkillCrawlerService(r), r

    def test_alle_quellen_am_netz_gescheitert_schreibt_keinen_leeren_katalog(self):
        c, redis = self._crawler()
        with patch.object(c, "_load_db_sources", return_value=[]), \
             patch("app.services.skill_crawler._env_repos", lambda: ["a/b", "c/d"]), \
             patch.object(c, "_crawl_github", side_effect=httpx.ConnectError(
                 "[Errno -3] Temporary failure in name resolution")), \
             patch.object(c, "_sync_to_db", return_value=None):
            ergebnis = _lauf(c.crawl())

        self.assertEqual(ergebnis, [])
        self.assertEqual(redis.gesetzt, [],
                         "leerer Katalog wurde in den Zwischenspeicher geschrieben")

    def test_inhaltlich_leeres_ergebnis_wird_weiterhin_geschrieben(self):
        # Gegenprobe: ohne Verbindungsfehler ist "0 Skills" eine echte Aussage
        # und muss weiterhin in den Zwischenspeicher gehen — sonst veraltet der
        # Katalog, wenn oben wirklich nichts mehr liegt.
        c, redis = self._crawler()
        with patch.object(c, "_load_db_sources", return_value=[]), \
             patch("app.services.skill_crawler._env_repos", lambda: ["a/b"]), \
             patch.object(c, "_crawl_github", return_value=[]), \
             patch.object(c, "_sync_to_db", return_value=None), \
             patch("app.core.vorlagen_skills.vorlagen_aktualisieren", return_value=None):
            _lauf(c.crawl())

        self.assertEqual(len(redis.gesetzt), 1, "echtes Null-Ergebnis muss gespeichert werden")
        self.assertIn("skill_count", redis.gesetzt[0][1])


if __name__ == "__main__":
    unittest.main()
