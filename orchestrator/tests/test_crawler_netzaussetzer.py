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

from app.core.verbindungswiederholung import (
    PAUSEN_HINTERGRUND_S,
    NetzaussetzerAbbruch,
    Netzwache,
    VerbindungNichtZustandeGekommen,
    ist_git_netzfehler,
)
from app.services.gesetz_crawler import GesetzCrawlerService, _AusfallZaehler


def _lauf(coro):
    return asyncio.run(coro)


class _ZipAttrappe:
    """Steht fuer ein ZIP mit genau einer XML-Datei — ohne echtes ZIP zu bauen."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def namelist(self): return ["norm.xml"]
    def read(self, name): return b"<?xml version=\"1.0\"?><dokumente></dokumente>"


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

    def test_netzaussetzer_erkannt_wenn_lauf_schlechter_als_bisher(self):
        netz = self._zaehler(verbindung=4)
        # Gar nichts geschrieben: eindeutig.
        self.assertTrue(GesetzCrawlerService._netzaussetzer(netz, 0, 0))
        # DER TEIL-LAUF — der Fall, den die erste Fassung durchliess: ein Aussetzer
        # MITTEN im Lauf ueber rund 6000 Normen bricht ihn nicht ab, er kuerzt ihn.
        # "Gerade gecrawlt, 40 Normen" statt 6123 ist dieselbe Falschaussage wie
        # "0 Normen", nur unauffaelliger.
        self.assertTrue(GesetzCrawlerService._netzaussetzer(netz, 40, 6123))
        # Lauf mindestens so gut wie bisher: normaler Lauf, Stempel darf weiter.
        self.assertFalse(GesetzCrawlerService._netzaussetzer(netz, 6123, 6123))
        self.assertFalse(GesetzCrawlerService._netzaussetzer(netz, 6200, 6123))
        # Nichts geschrieben, aber die Fehler lagen am Inhalt -> echter Befund,
        # kein Netz. Der Stempel muss weiterruecken, sonst verdeckt der Fix einen
        # echten Mangel an der Quelle.
        inhalt = self._zaehler(verbindung=0, inhalt=4)
        self.assertFalse(GesetzCrawlerService._netzaussetzer(inhalt, 0, 6123))

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

    def _crawler(self, bisher: int = 0):
        """Crawler mit Redis-Attrappe. ``bisher`` ist der Katalogstand VOR dem Lauf."""
        import json as _json
        from app.services.skill_crawler import SkillCrawlerService

        class Redis:
            def __init__(self, bisher):
                self.gesetzt = []
                self.vorstand = (
                    _json.dumps({"skills": [], "skill_count": bisher}) if bisher else None
                )
            class _C:
                def __init__(self, aussen): self.aussen = aussen
                async def set(self, key, wert, ex=None): self.aussen.gesetzt.append((key, wert))
                async def get(self, key): return self.aussen.vorstand
            @property
            def client(self): return Redis._C(self)

        r = Redis(bisher)
        return SkillCrawlerService(r), r

    def test_alle_quellen_am_netz_gescheitert_schreibt_keinen_leeren_katalog(self):
        c, redis = self._crawler()
        with patch.object(c, "_load_db_sources", return_value=[]), \
             patch("app.services.skill_crawler._env_repos", lambda: ["a/b", "c/d"]), \
             patch.object(c, "_crawl_github", side_effect=httpx.ConnectError(
                 "[Errno -3] Temporary failure in name resolution")), \
             patch.object(c, "_sync_to_db", return_value=None):
            # Abbruch als AUSNAHME, nicht als leere Liste: ein leeres Ergebnis ist
            # von "es gibt wirklich nichts" nicht zu unterscheiden, und die
            # Oberflaeche meldete daraufhin "ok, 0 importiert" (#746).
            with self.assertRaises(NetzaussetzerAbbruch):
                _lauf(c.crawl())

        self.assertEqual(redis.gesetzt, [],
                         "leerer Katalog wurde in den Zwischenspeicher geschrieben")

    def test_teilweise_am_netz_gescheitert_kuerzt_den_katalog_nicht(self):
        # Die unauffaellige Haelfte derselben Fehlerklasse: fallen 18 von 20
        # Quellen ans Netz, stand bisher ein auf wenige Eintraege gekuerzter
        # Katalog eine WOCHE lang als frisch da (REDIS_TTL = 7 Tage).
        c, redis = self._crawler(bisher=20)

        async def eine_quelle(_client, repo, **k):
            if repo == "gut/repo":
                return [{"name": "a", "description": "", "content": ""}]
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

        with patch.object(c, "_load_db_sources", return_value=[]), \
             patch("app.services.skill_crawler._env_repos",
                   lambda: ["gut/repo", "weg/eins", "weg/zwei"]), \
             patch.object(c, "_crawl_github", side_effect=eine_quelle), \
             patch.object(c, "_sync_to_db", return_value=None):
            with self.assertRaises(NetzaussetzerAbbruch):
                _lauf(c.crawl())

        self.assertEqual(redis.gesetzt, [],
                         "gekuerzter Katalog wurde als frisch gespeichert")

    def test_gewachsener_katalog_wird_trotz_einzelner_netzfehler_gespeichert(self):
        # Gegenprobe zur Verschaerfung: ein Lauf, der trotz eines Aussetzers mehr
        # findet als bisher, ist ein GUTER Lauf. Ohne diesen Test koennte die Regel
        # zu "bei jedem Verbindungsfehler nichts speichern" entgleisen — der
        # Katalog wuerde dann nie mehr frisch.
        c, redis = self._crawler(bisher=1)

        async def gemischt(_client, repo, **k):
            if repo == "weg/eins":
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            return [{"name": repo, "description": "", "content": ""}]

        with patch.object(c, "_load_db_sources", return_value=[]), \
             patch("app.services.skill_crawler._env_repos",
                   lambda: ["gut/a", "gut/b", "weg/eins"]), \
             patch.object(c, "_crawl_github", side_effect=gemischt), \
             patch.object(c, "_sync_to_db", return_value=None), \
             patch("app.core.vorlagen_skills.vorlagen_aktualisieren", return_value=None):
            ergebnis = _lauf(c.crawl())

        self.assertEqual(len(ergebnis), 2)
        self.assertEqual(len(redis.gesetzt), 1,
                         "gewachsener Katalog muss gespeichert werden")

    def test_datenbank_quelle_am_netz_gescheitert_wird_gezaehlt(self):
        # Der Zweig fuer Quellen AUS DER DATENBANK hatte seine eigene
        # Fehlerklassen-Zaehlung und war ungedeckt (der Gegenleser fand ihn als
        # ueberlebenden Mutanten): kein Test fuhr je ueber ``_load_db_sources``.
        c, redis = self._crawler(bisher=5)
        quelle = {"id": 1, "name": "Forgejo", "location": "https://git.example.invalid/x/y",
                  "kind": "git", "trusted": True, "created_by": "admin"}

        async def klon_weg(_src):
            raise VerbindungNichtZustandeGekommen(
                "git clone failed (Netz): fatal: Could not resolve host: git.example.invalid")

        with patch.object(c, "_load_db_sources", return_value=[quelle]), \
             patch("app.services.skill_crawler._env_repos", lambda: []), \
             patch.object(c, "_crawl_git_source", side_effect=klon_weg), \
             patch.object(c, "_update_source_status", return_value=None), \
             patch.object(c, "_sync_to_db", return_value=None):
            with self.assertRaises(NetzaussetzerAbbruch):
                _lauf(c.crawl())

        self.assertEqual(redis.gesetzt, [],
                         "Netzausfall einer DB-Quelle wurde als echtes Null-Ergebnis gewertet")

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


class NetzwacheTests(unittest.TestCase):
    """Die Wache beantwortet ZWEI Fragen und darf sie nicht verwechseln."""

    def test_inhaltsfehler_setzt_die_serie_zurueck(self):
        w = Netzwache("Test", grenze=3)
        w.melde(httpx.ConnectError("name resolution"))
        w.melde(httpx.ConnectError("name resolution"))
        # Ein Fehler AM INHALT beweist, dass die Verbindung stand -> Serie neu.
        w.melde(ValueError("kaputtes XML"))
        self.assertEqual(w.anzahl, 2, "Inhaltsfehler darf nicht als Netzfehler zaehlen")
        self.assertEqual(w.serie, 0)
        self.assertFalse(w.abbrechen)

    def test_geglueckter_abruf_setzt_die_serie_zurueck(self):
        w = Netzwache("Test", grenze=2)
        w.melde(httpx.ConnectError("name resolution"))
        w.erfolg()
        w.melde(httpx.ConnectError("name resolution"))
        self.assertFalse(w.abbrechen, "ein Erfolg zwischendurch ist kein Dauerausfall")
        w.melde(httpx.ConnectError("name resolution"))
        self.assertTrue(w.abbrechen)

    def test_grenze_ist_zur_laufzeit_einstellbar(self):
        # Waere die Grenze ein Standardargument, haette sie die Definitionszeit
        # gebunden und ein Test koennte sie nicht setzen (dieselbe Falle, die bei
        # den Pausen schon dokumentiert ist).
        with patch("app.core.verbindungswiederholung.HINTERGRUND_ABBRUCH_NACH", 1):
            w = Netzwache("Test")
            w.melde(httpx.ConnectError("name resolution"))
            self.assertTrue(w.abbrechen)


class GitNetzfehlerTests(unittest.TestCase):
    """``git clone`` meldet einen Netzausfall nicht als Netzausfall — bis jetzt."""

    def test_markertext_wird_erkannt(self):
        self.assertTrue(ist_git_netzfehler(
            "fatal: unable to access 'https://git.example.invalid/x/y/': "
            "Could not resolve host: git.example.invalid"))
        self.assertTrue(ist_git_netzfehler("ssh: connect to host ... Connection timed out"))
        # Gegenprobe: ein echter Mangel an der Quelle darf NICHT als Netz gelten,
        # sonst verdeckt der Fix eine falsch eingetragene Quelle.
        self.assertFalse(ist_git_netzfehler(
            "remote: Repository not found.\nfatal: repository ... not found"))
        self.assertFalse(ist_git_netzfehler("fatal: Authentication failed"))

    def test_klon_mit_netzfehler_wirft_verbindungsfehler(self):
        from app.services.skill_crawler import SkillCrawlerService

        c = SkillCrawlerService(None)
        quelle = {"id": 1, "name": "q", "location": "https://git.example.invalid/x/y",
                  "kind": "git", "trusted": False}

        class Proc:
            returncode = 128
            async def communicate(self):
                return (b"", b"fatal: unable to access: Could not resolve host: git.example.invalid")

        async def start(*a, **k):
            return Proc()

        with patch("asyncio.create_subprocess_exec", start):
            with self.assertRaises(VerbindungNichtZustandeGekommen):
                _lauf(c._crawl_git_source(quelle))

    def test_klon_mit_echtem_mangel_bleibt_ein_gewoehnlicher_fehler(self):
        from app.services.skill_crawler import SkillCrawlerService

        c = SkillCrawlerService(None)
        quelle = {"id": 1, "name": "q", "location": "https://git.example.invalid/x/y",
                  "kind": "git", "trusted": False}

        class Proc:
            returncode = 128
            async def communicate(self):
                return (b"", b"remote: Repository not found.")

        async def start(*a, **k):
            return Proc()

        with patch("asyncio.create_subprocess_exec", start):
            with self.assertRaises(RuntimeError) as gefangen:
                _lauf(c._crawl_git_source(quelle))
        self.assertNotIsInstance(gefangen.exception, VerbindungNichtZustandeGekommen)


class InnereSchleifeTests(unittest.TestCase):
    """Der Aussetzer NACH dem Baum-Abruf war unsichtbar — die Datei-Abrufe schluckten ihn."""

    class _BaumDannWeg:
        """Baum-Abruf klappt, danach faellt das Netz fuer alle Dateien aus."""

        def __init__(self, dateien: int):
            self.dateien = dateien
            self.abrufe = 0

        async def get(self, ziel, *a, **k):
            self.abrufe += 1
            if "git/trees" in ziel:
                class Antwort:
                    status_code = 200
                    def json(_selbst):
                        return {"tree": [
                            {"type": "blob", "path": f"skills/s{i}/SKILL.md"}
                            for i in range(3)
                        ]}
                return Antwort()
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

    def test_datei_abrufe_am_netz_gescheitert_werden_gezaehlt(self):
        from app.services.skill_crawler import SkillCrawlerService

        c = SkillCrawlerService(None)
        wache = Netzwache("Test")
        client = self._BaumDannWeg(3)
        with patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.services.skill_crawler.PAUSEN_HINTERGRUND_S", (0.0,)):
            ergebnis = _lauf(c._crawl_github(client, "a/b", wache=wache))

        self.assertEqual(ergebnis, [])
        self.assertGreater(
            wache.anzahl, 0,
            "Netzausfall der Datei-Abrufe blieb unsichtbar — die Quelle waere als "
            "'ok: 0 skills' vermerkt worden",
        )

    def test_rohdatei_wird_nach_einem_verbindungsfehler_doch_geholt(self):
        # Diese Aufrufstelle war ungedeckt (ueberlebender Mutant des Gegenlesers):
        # der alte Test lieferte einen LEEREN Baum, die Datei-Schleife lief nie.
        from app.services.skill_crawler import SkillCrawlerService

        class Client:
            def __init__(self):
                self.roh_abrufe = 0

            async def get(self, ziel, *a, **k):
                if "git/trees" in ziel:
                    class Baum:
                        status_code = 200
                        def json(_s): return {"tree": [
                            {"type": "blob", "path": "skills/eins/SKILL.md"}]}
                    return Baum()
                self.roh_abrufe += 1
                if self.roh_abrufe == 1:
                    raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
                class Inhalt:
                    status_code = 200
                    text = (
                        "---\nname: eins\ndescription: Testskill\n---\n\n"
                        "Inhalt des Skills.\n"
                    )
                return Inhalt()

        c = SkillCrawlerService(None)
        client = Client()
        with patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.services.skill_crawler.PAUSEN_HINTERGRUND_S", (0.0,)):
            ergebnis = _lauf(c._crawl_github(client, "a/b"))

        self.assertEqual(client.roh_abrufe, 2, "der Datei-Abruf wurde nicht wiederholt")
        self.assertEqual(len(ergebnis), 1, "der Skill wurde nach der Wiederholung nicht gelesen")


    def test_de_lauf_bricht_nach_einer_serie_ab(self):
        # Eigener Test fuer den DE-Pfad: er hat seine eigene Abbruchstelle. Ohne
        # ihn bliebe deren Entfernen gruen (Lehre aus der ersten Batterie, in der
        # genau die DE-Wache als einzige ungedeckt war).
        c = GesetzCrawlerService()
        c.law_count = 6123

        class Weg:
            def __init__(self): self.ziele = []
            async def get(self, ziel, *a, **k):
                self.ziele.append(ziel)
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        async def toc(_selbst, _client):
            return [(f"Norm {i}", f"norm{i}") for i in range(10)]

        client = Weg()
        with patch("app.services.gesetz_crawler.httpx.AsyncClient", lambda *a, **k: client), \
             patch("app.services.gesetz_crawler._vorhandene_teile", lambda *a: {}), \
             patch.object(GesetzCrawlerService, "_fetch_toc", toc), \
             patch("app.core.verbindungswiederholung.HINTERGRUND_ABBRUCH_NACH", 2), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)):
            _lauf(c.crawl())

        self.assertLess(len(set(client.ziele)), 10,
                        "der DE-Lauf hat trotz Dauerausfall jede Norm angefasst")

    def test_de_teil_lauf_laesst_stempel_und_zahl_stehen(self):
        # Der Fall, den die erste Fassung des Fixes durchliess: der Aussetzer
        # trifft NICHT alles. Eine Norm geht durch, 6122 nicht — und die Anzeige
        # meldete "gerade gecrawlt, 1 Norm" statt 6123.
        c = GesetzCrawlerService()
        c.last_crawled_at = "2026-10-12T04:00:00+00:00"
        c.law_count = 6123

        class EineGeht:
            async def get(self, ziel, *a, **k):
                if ziel.endswith("gut/xml.zip"):
                    class Antwort:
                        status_code = 200
                        content = b"egal"
                    return Antwort()
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        async def toc(_selbst, _client):
            return [("Gute Norm", "gut"), ("Weg", "weg1"), ("Weg", "weg2")]

        async def schreiben(*a, **k):
            return None

        with patch("app.services.gesetz_crawler.httpx.AsyncClient", lambda *a, **k: EineGeht()), \
             patch("app.services.gesetz_crawler._vorhandene_teile", lambda *a: {}), \
             patch.object(GesetzCrawlerService, "_fetch_toc", toc), \
             patch("app.services.gesetz_crawler._gesetz_schreiben", schreiben), \
             patch("app.services.gesetz_crawler.zipfile.ZipFile", _ZipAttrappe), \
             patch("app.services.gesetz_crawler.parse_norm_xml",
                   lambda *a, **k: {"titel": "GG", "teile": []}), \
             patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)):
            geschrieben = _lauf(c.crawl())

        self.assertEqual(geschrieben, 1, "die eine erreichbare Norm muss geschrieben werden")
        self.assertEqual(c.last_crawled_at, "2026-10-12T04:00:00+00:00",
                         "Teil-Lauf hat den Frischestempel weitergerueckt")
        self.assertEqual(c.law_count, 6123, "Teil-Lauf hat die Normenzahl auf 1 gekuerzt")


class AbbruchbudgetTests(unittest.TestCase):
    """Die laengere Leiter darf einen Lauf nicht tagelang festhalten."""

    def test_eu_lauf_bricht_nach_einer_serie_ab_statt_alle_normen_abzuwarten(self):
        c = GesetzCrawlerService()

        class Weg:
            def __init__(self): self.ziele = []
            async def get(self, ziel, *a, **k):
                self.ziele.append(ziel)
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        client = Weg()
        normen = [(f"3202{i}R0001", f"Norm {i}") for i in range(10)]
        with patch("app.services.gesetz_crawler.httpx.AsyncClient", lambda *a, **k: client), \
             patch("app.services.gesetz_crawler._vorhandene_teile", lambda *a: {}), \
             patch("app.services.gesetz_crawler.EU_CURATED_LAWS", normen), \
             patch("app.core.verbindungswiederholung.HINTERGRUND_ABBRUCH_NACH", 2), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)):
            geschrieben = _lauf(c.crawl_eu())

        self.assertEqual(geschrieben, 0)
        # Ohne Budget waeren alle 10 Normen je 2x versucht worden. Im Ernstfall sind
        # es rund 6000 Normen x 40 s = etwa 68 Stunden in EINEM Lauf.
        verschiedene = len(set(client.ziele))
        self.assertLess(verschiedene, 10,
                        "der Lauf hat trotz Dauerausfall jede Norm angefasst")
        self.assertLessEqual(verschiedene, 3)


class OberflaecheTests(unittest.TestCase):
    """"Aktualisieren" darf einen Netzaussetzer nicht als Erfolg mit 0 Treffern melden."""

    def test_refresh_endpunkt_antwortet_mit_fehler_statt_null_skills(self):
        from fastapi import HTTPException

        from app.api.skills_catalog import refresh_skill_catalog

        class Crawler:
            async def crawl(self):
                raise NetzaussetzerAbbruch("Skill crawler: nur 0 von bisher 42 Skills")

        class Anfrage:
            class app:
                class state:
                    skill_crawler = Crawler()

        with self.assertRaises(HTTPException) as gefangen:
            _lauf(refresh_skill_catalog(Anfrage(), user=None))

        self.assertEqual(gefangen.exception.status_code, 503)
        self.assertIn("Netzaussetzer", gefangen.exception.detail)


class GesetzAufrufstellenTests(unittest.TestCase):
    """Die beiden DE-Aufrufstellen waren ungedeckt (ueberlebende Mutanten)."""

    class _EinmalWeg:
        """Scheitert beim ERSTEN Abruf je Ziel, danach Erfolg."""

        def __init__(self, antwort):
            self.antwort = antwort
            self.gescheitert: set = set()
            self.abrufe = 0

        async def get(self, ziel, *a, **k):
            self.abrufe += 1
            if ziel not in self.gescheitert:
                self.gescheitert.add(ziel)
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            return self.antwort

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    def test_inhaltsverzeichnis_wird_nach_einem_verbindungsfehler_doch_geholt(self):
        class Antwort:
            status_code = 200
            content = (b'<?xml version="1.0"?><items>'
                       b'<item><title>Grundgesetz</title><link>/gg/xml.zip</link></item>'
                       b'</items>')
            def raise_for_status(self): return None

        client = self._EinmalWeg(Antwort())
        c = GesetzCrawlerService()
        with patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)):
            _lauf(c._fetch_toc(client))

        self.assertEqual(client.abrufe, 2, "das Inhaltsverzeichnis wurde nicht wiederholt")

    def test_einzelne_norm_wird_nach_einem_verbindungsfehler_doch_geholt(self):
        import io as _io
        import zipfile as _zip

        puffer = _io.BytesIO()
        with _zip.ZipFile(puffer, "w") as zf:
            zf.writestr("gg.xml", '<?xml version="1.0"?><dokumente></dokumente>')

        class Antwort:
            status_code = 200
            content = puffer.getvalue()

        client = self._EinmalWeg(Antwort())
        c = GesetzCrawlerService()
        with patch("app.core.verbindungswiederholung.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.services.gesetz_crawler.PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch("app.services.gesetz_crawler.parse_norm_xml",
                   lambda *a, **k: {"titel": "GG", "teile": []}):
            ergebnis = _lauf(c._fetch_one(client, "gg"))

        self.assertEqual(client.abrufe, 2, "der Norm-Abruf wurde nicht wiederholt")
        self.assertIsNotNone(ergebnis, "die Norm wurde nach der Wiederholung nicht gelesen")


if __name__ == "__main__":
    unittest.main()
