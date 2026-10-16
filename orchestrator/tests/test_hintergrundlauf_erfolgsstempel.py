"""Ein am Netz gescheiterter Hintergrundlauf darf keinen Erfolgsstempel schreiben.

Fehlerklasse (#977, Geschwister von #746): ein Lauf, der nichts geholt hat, weil
die Verbindung weg war oder die Antwort keine Daten enthielt, schreibt trotzdem
einen frischen Stempel mit Status "ok". Wer danach in die Oberflaeche sieht, kann
einen Netzaussetzer nicht von einem echten Leerstand unterscheiden.

Drei Hintergrund-Pfade, drei verschiedene Auspraegungen derselben Klasse:

1. ``knowledge_feed_service`` — der Scheduler ruft ``tick()`` alle 5 Minuten; drei
   der vier Quell-Plugins fingen jede Ausnahme je Quelle ab und machten
   ``continue``. Bei einem Namensaufloesungs-Ausfall kam ``[]`` zurueck und
   ``_run_single_feed`` schrieb ``last_status="ok"``, ``last_error=None``.
   AM QUELLTEXT KORRIGIERT: ``fetch_mcp_registry`` hatte als einziges Plugin KEIN
   ``except`` — dort war ein Verbindungsfehler schon vorher sichtbar. Die
   Praemisse "alle vier verschlucken" aus dem Issue traf also nur auf drei zu.
2. ``trend_service`` — setzte den Frischestempel VOR dem Scan. Ein verlorener
   Scan verbrannte damit das 23-Stunden-Fenster, ohne etwas geholt zu haben.
3. ``skill_crawler`` — liefert der Baum-Abruf 403 (GitHub-Limit ohne Schluessel)
   oder 404, gab ``_crawl_github`` still ``[]`` zurueck. Das ist KEIN
   Verbindungsfehler, die Wache aus #746 greift dort also bewusst nicht — der
   Stempel rueckte mit 0 Skills weiter.

Deshalb unterscheidet die Netzwache jetzt zwei Zahlen: ``anzahl`` (Verbindung kam
nicht zustande) und ``abrufausfaelle`` (Antwort kam an, enthielt keine Daten).
Beide beantworten "ist das Ergebnis leer, weil es nichts gibt, oder weil wir es
nicht geholt haben" — nur die erste darf einen Lauf abbrechen lassen.

Nicht in diesem Lauf behoben: ``model_registry_service.discover`` (Punkt 3 des
Issues). Das ist kein Hintergrundlauf, sondern administrator-ausgeloest, und der
Fix braucht eine andere Entscheidung (alten Modellstand behalten statt mit einer
leeren Liste ueberschreiben). Bleibt als eigener Punkt am Issue stehen.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.verbindungswiederholung import Netzwache, lauf_unvollstaendig


def _lauf(coro):
    return asyncio.run(coro)


class _Antwort:
    """Minimale httpx-Antwort — nur was die geprueften Pfade lesen."""

    def __init__(self, status: int, nutzlast=None, text: str = ""):
        self.status_code = status
        self._nutzlast = nutzlast
        self.text = text

    def json(self):
        return self._nutzlast

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None,  # type: ignore[arg-type]
            )


class _Klient:
    """httpx.AsyncClient-Attrappe mit vorgegebenen Antworten je Aufruf."""

    def __init__(self, antworten):
        self._antworten = list(antworten)
        self.aufrufe = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        self.aufrufe += 1
        if not self._antworten:
            # NICHT stillschweigend etwas zurueckgeben: ein unerwarteter Abruf gab
            # vorher die leere Liste als "Antwort" zurueck, der Zugriff auf
            # ``status_code`` lief in einen AttributeError und der Lauf galt als
            # "ok". Ein Test mit einer zu kurzen Antwortliste waere damit gruen
            # geblieben, ohne den geprueften Pfad je zu erreichen.
            raise AssertionError(
                f"unerwarteter Abruf Nr. {self.aufrufe} — Antwortliste zu kurz")
        naechste = self._antworten.pop(0)
        if isinstance(naechste, BaseException):
            raise naechste
        return naechste


def _netzfehler():
    return httpx.ConnectError("Temporary failure in name resolution")


class EntscheidungsfunktionTests(unittest.TestCase):
    """``lauf_unvollstaendig`` muss die zweite Ausfallklasse mitrechnen."""

    def test_antwort_ohne_daten_haelt_den_stempel_genauso_auf(self):
        # Genau der Fall aus Punkt 4: kein Verbindungsfehler, aber auch keine
        # Daten. Ohne ``weitere_ausfaelle`` waere das Ergebnis False und der
        # Stempel wuerde mit 0 Skills weiterruecken.
        self.assertTrue(lauf_unvollstaendig(
            erzeugt=0, bisher=12, verbindungsausfaelle=0, weitere_ausfaelle=3))

    def test_ohne_jeden_ausfall_rueckt_der_stempel_weiter(self):
        # Eine echte Leerwoche darf nicht als Netzaussetzer gelten, sonst
        # blockiert sie den Stempel auf Dauer.
        self.assertFalse(lauf_unvollstaendig(
            erzeugt=0, bisher=12, verbindungsausfaelle=0, weitere_ausfaelle=0))

    def test_abrufausfall_bricht_den_lauf_nicht_ab(self):
        # Eine Quelle, die dauerhaft 404 liefert, darf den ganzen Lauf nicht
        # beenden — nur eine SERIE von Verbindungsfehlern darf das.
        wache = Netzwache("Test", grenze=2)
        wache.melde_abruf("a: HTTP 404")
        wache.melde_abruf("b: HTTP 404")
        wache.melde_abruf("c: HTTP 404")
        self.assertEqual(wache.abrufausfaelle, 3)
        self.assertEqual(wache.anzahl, 0)
        self.assertFalse(wache.abbrechen)

    def test_antwort_setzt_die_fehlerserie_zurueck(self):
        # Eine Antwort beweist, dass die Verbindung stand.
        wache = Netzwache("Test", grenze=2)
        wache.melde(_netzfehler())
        wache.melde_abruf("b: HTTP 403")
        wache.melde(_netzfehler())
        self.assertEqual(wache.serie, 1)
        self.assertFalse(wache.abbrechen)


class WissensFeedTests(unittest.TestCase):
    """Punkt 1 — die wichtigste Stelle: Status in der Oberflaeche."""

    def _feed(self, feed_type="ai_news", config=None):
        class _Feed:
            id = 7
            items_count = 0
            last_run_at = None
            last_status = None
            last_error = None

        f = _Feed()
        f.feed_type = feed_type
        f.config = config or {}
        return f

    def test_netzausfall_meldet_fehler_statt_ok(self):
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(config={"sources": ["https://example.invalid/rss"]})
        session = AsyncMock()
        klient = _Klient([_netzfehler(), _netzfehler(), _netzfehler(), _netzfehler()])
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch.object(kfs, "PAUSEN_HINTERGRUND_S", (0.0, 0.0, 0.0)):
            anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertEqual(anzahl, 0)
        self.assertIsNotNone(
            fehler, "ein Lauf ohne einen einzigen erfolgreichen Abruf darf nicht 'ok' melden")
        # Die ZAHLEN pruefen, nicht das Wort: der Satz enthaelt "an der Verbindung"
        # immer, auch wenn der Fehler in der falschen Klasse gelandet ist. Erst die
        # Zahl davor trennt "Verbindung kam nicht zustande" von "Antwort ohne Daten".
        self.assertIn("1 Quelle(n) an der Verbindung", fehler)
        self.assertIn("0 ohne Daten", fehler)
        # Zweite Zusicherung: der Stempel darf auch nicht nebenbei gesetzt worden
        # sein. Ein Test, der nur den Rueckgabewert prueft, bleibt gruen, waehrend
        # die Wirkung weiter eintritt.
        self.assertIsNone(feed.last_run_at)
        self.assertNotEqual(feed.last_status, "ok")
        session.commit.assert_not_awaited()

    def test_http_403_auf_alle_quellen_meldet_fehler(self):
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(feed_type="best_practices",
                          config={"sources": ["https://example.invalid/a"]})
        session = AsyncMock()
        klient = _Klient([_Antwort(403, {})])
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient):
            anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertEqual(anzahl, 0)
        self.assertIsNotNone(fehler, "HTTP 403 auf alle Quellen ist kein Leerstand")
        self.assertIn("0 Quelle(n) an der Verbindung", fehler)
        self.assertIn("1 ohne Daten", fehler)
        self.assertIsNone(feed.last_run_at)

    def test_echter_leerstand_bleibt_ok(self):
        # Die Gegenprobe zur Wache: eine Quelle, die erreichbar ist und wirklich
        # nichts liefert, muss weiter "ok" melden. Sonst waere der Fix eine
        # Dauerstoerung statt einer Diagnose.
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(feed_type="competitor", config={"sources": {"x": "https://example.invalid/r"}})
        session = AsyncMock()
        klient = _Klient([_Antwort(200, [])])
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient):
            anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertIsNone(fehler)
        self.assertEqual(anzahl, 0)
        self.assertEqual(feed.last_status, "ok")
        self.assertIsNone(feed.last_error)
        self.assertIsNotNone(feed.last_run_at)

    def test_teil_lauf_bleibt_ok_nennt_den_ausfall_aber(self):
        # Eine von zwei Quellen weg: der Lauf hat etwas geliefert, der Stempel
        # rueckt weiter — aber der halbe Lauf darf nicht wie ein ganzer aussehen.
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(config={"sources": ["https://example.invalid/a",
                                              "https://example.invalid/b"]})
        session = AsyncMock()
        session.execute = AsyncMock(return_value=type("R", (), {"rowcount": 1})())
        rss = ("<rss><channel><item><title>T</title><link>https://example.invalid/x</link>"
               "</item></channel></rss>")
        klient = _Klient([_Antwort(200, None, rss), _netzfehler(), _netzfehler(),
                          _netzfehler(), _netzfehler()])
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch.object(kfs, "PAUSEN_HINTERGRUND_S", (0.0, 0.0, 0.0)):
            anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertIsNone(fehler)
        self.assertEqual(feed.last_status, "ok")
        self.assertIsNotNone(feed.last_error)
        self.assertIn("Teil-Lauf", feed.last_error)

    def test_jedes_plugin_nimmt_die_wache_an(self):
        # Formtest gegen die stille Variante dieses Fehlers: ein neues Plugin,
        # das die Wache nicht annimmt, wuerde beim Aufruf mit TypeError sterben —
        # aber erst im Betrieb. Hier faellt es sofort auf.
        import inspect

        from app.services import knowledge_feed_service as kfs

        self.assertEqual(len(kfs.FEED_PLUGINS), 4)
        for name, plugin in kfs.FEED_PLUGINS.items():
            parameter = list(inspect.signature(plugin).parameters)
            self.assertEqual(
                len(parameter), 2,
                f"Plugin '{name}' muss (config, wache) annehmen, hat {parameter}")


    def test_lesetimeout_wird_mitgezaehlt(self):
        # Die Luecke, die das Gegenlesen fand: ``Netzwache.melde`` erhoeht NUR bei
        # ConnectError/ConnectTimeout/PoolTimeout/ProxyError. Ein ReadTimeout ist
        # der haeufigste Aussetzer ueberhaupt (langsame Gegenstelle, Paketverlust
        # nach dem Handshake) und wurde von keinem Zaehler erfasst — der Lauf galt
        # wieder als "ok".
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(config={"sources": ["https://example.invalid/rss"]})
        session = AsyncMock()
        klient = _Klient([httpx.ReadTimeout("zu langsam")])
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient):
            anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertIsNotNone(fehler, "ein Lesetimeout ist kein Leerstand")
        self.assertIn("1 ohne Daten", fehler)
        self.assertEqual(anzahl, 0)
        self.assertIsNone(feed.last_run_at)

    def test_lesetimeout_in_jedem_plugin_mitgezaehlt(self):
        # Dieselbe Luecke steckt in jedem der drei Plugins mit ``continue``. Nur
        # ``ai_news`` zu pruefen liess zwei Mutationen der Batterie ueberleben —
        # also wird jedes Plugin einzeln gemessen, mit seiner eigenen Konfiguration.
        from app.services import knowledge_feed_service as kfs

        faelle = [
            ("ai_news", {"sources": ["https://example.invalid/a"]}),
            ("competitor", {"sources": {"x": "https://example.invalid/b"}}),
            ("best_practices", {"sources": ["https://example.invalid/c"]}),
        ]
        for feed_type, config in faelle:
            with self.subTest(feed_type=feed_type):
                feed = self._feed(feed_type=feed_type, config=config)
                klient = _Klient([httpx.ReadTimeout("zu langsam")])
                with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient):
                    _anzahl, fehler = _lauf(kfs._run_single_feed(AsyncMock(), feed))
                self.assertIsNotNone(
                    fehler, f"{feed_type}: Lesetimeout darf nicht als Leerstand gelten")
                self.assertIn("1 ohne Daten", fehler)
                self.assertIsNone(feed.last_run_at)

    def test_mcp_registry_antwort_ohne_liste_meldet_fehler(self):
        # Das einzige Plugin mit abweichendem Fehlerpfad (Ausnahme statt
        # ``continue``). Geprueft wird der neue Zweig: eine Antwort, die 200 ist,
        # aber keine Liste enthaelt — vorher "ok, 0 Eintraege".
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(feed_type="mcp_registry",
                          config={"source_url": "https://example.invalid/katalog"})
        session = AsyncMock()
        klient = _Klient([_Antwort(200, {"message": "Not Found"})])
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient):
            anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertIsNotNone(fehler)
        self.assertIn("1 ohne Daten", fehler)
        self.assertEqual(anzahl, 0)

    def test_mcp_registry_verbindungsfehler_bleibt_fehler(self):
        # Gegenprobe zur Praemissen-Korrektur: hier war der Netzausfall schon
        # vorher sichtbar (kein ``except``). Das muss so bleiben.
        from app.services import knowledge_feed_service as kfs

        feed = self._feed(feed_type="mcp_registry")
        session = AsyncMock()
        klient = _Klient([_netzfehler()] * 4)
        with patch.object(kfs.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch.object(kfs, "PAUSEN_HINTERGRUND_S", (0.0, 0.0, 0.0)):
            _anzahl, fehler = _lauf(kfs._run_single_feed(session, feed))

        self.assertIsNotNone(fehler)
        self.assertIn("plugin raised", fehler)


class TrendStempelTests(unittest.TestCase):
    """Punkt 2 — der Stempel gehoert hinter den Scan, nicht davor."""

    def _dienst(self):
        from app.services.trend_service import TrendService

        d = TrendService()
        d._lauf_merken = AsyncMock()
        d._letzter_lauf = AsyncMock(return_value=None)
        return d

    def test_netzausfall_verbrennt_das_fenster_nicht(self):
        from app.services import trend_service as ts

        d = self._dienst()
        d._get_existing_source_repos = AsyncMock(return_value=set())
        klient = _Klient([_netzfehler()] * 40)
        with patch.object(ts.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch.object(ts, "PAUSEN_HINTERGRUND_S", (0.0, 0.0, 0.0)), \
             patch("app.config.settings.skill_trend_scan_enabled", True, create=True):
            ergebnis = _lauf(d.tick())

        self.assertTrue(ergebnis.get("netzausfall"), ergebnis)
        d._lauf_merken.assert_not_awaited()
        self.assertIsNotNone(d._letzter_fehlversuch)

    def test_nach_fehlversuch_nicht_sofort_wieder_abrufen(self):
        # Der Scheduler ruft tick() alle 30 s. Ohne Pause waeren das 120
        # Versuche je Stunde statt der einen, die der verbrannte Stempel kostete.
        d = self._dienst()
        d.scan = AsyncMock(side_effect=AssertionError("darf nicht abrufen"))
        d._letzter_fehlversuch = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc)
        with patch("app.config.settings.skill_trend_scan_enabled", True, create=True):
            ergebnis = _lauf(d.tick())
        self.assertTrue(ergebnis.get("netzpause"), ergebnis)

    def test_http_403_auf_alle_abrufe_verbrennt_das_fenster_nicht(self):
        # Der wahrscheinlichste Realfall: GitHub antwortet mit dem Rate-Limit, die
        # Verbindung steht. Ohne diesen Test bliebe eine Fassung gruen, die nur
        # ``wache.anzahl`` prueft — und genau die verbrennt dann das Fenster.
        from app.services import trend_service as ts

        d = self._dienst()
        d._get_existing_source_repos = AsyncMock(return_value=set())
        klient = _Klient([_Antwort(403, {})] * len(ts._GITHUB_SEARCH_QUERIES)
                         + [_Antwort(403, {})])
        with patch.object(ts.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch("app.config.settings.skill_trend_scan_enabled", True, create=True):
            ergebnis = _lauf(d.tick())

        self.assertTrue(ergebnis.get("netzausfall"), ergebnis)
        self.assertEqual(ergebnis["verbindungsausfaelle"], 0)
        d._lauf_merken.assert_not_awaited()

    def test_ausnahme_im_scan_setzt_die_fehlerpause(self):
        # Vorher stand ``_lauf_merken`` VOR dem Scan und hielt die 23-h-Sperre
        # auch dann, wenn der Scan mit einer Ausnahme endete. Ohne diesen Zweig
        # haette der Umbau aus einem Datenbank-Ausfall 120 Abrufreihen je Stunde
        # gemacht.
        d = self._dienst()
        d.scan = AsyncMock(side_effect=RuntimeError("DB weg"))
        with patch("app.config.settings.skill_trend_scan_enabled", True, create=True):
            with self.assertRaises(RuntimeError):
                _lauf(d.tick())
        self.assertIsNotNone(
            d._letzter_fehlversuch,
            "auch ein mit Ausnahme gescheiterter Lauf muss die Pause setzen")
        d._lauf_merken.assert_not_awaited()

    def test_echte_leerwoche_setzt_den_stempel(self):
        # Gegenprobe: erreichbar, aber nichts gefunden. Der Stempel MUSS weiter
        # ruecken, sonst laeuft der Scan alle 15 Minuten gegen eine leere Quelle.
        from app.services import trend_service as ts

        d = self._dienst()
        d._get_existing_source_repos = AsyncMock(return_value=set())
        # Laenge aus der Produktionskonstante, nicht als 4 hart hingeschrieben:
        # sonst bleibt der Test gruen, wenn eine fuenfte Suchanfrage dazukommt und
        # der HN-Abruf ueberhaupt keine Antwort mehr bekommt.
        klient = _Klient([_Antwort(200, {"items": []})] * len(ts._GITHUB_SEARCH_QUERIES)
                         + [_Antwort(200, {"hits": []})])
        with patch.object(ts.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch("app.config.settings.skill_trend_scan_enabled", True, create=True):
            ergebnis = _lauf(d.tick())

        self.assertFalse(ergebnis.get("netzausfall"), ergebnis)
        d._lauf_merken.assert_awaited_once()


class SkillCrawlerBaumTests(unittest.TestCase):
    """Punkt 4 — ein nicht geholter Baum ist kein leeres Repo."""

    class _Redis:
        """Redis-Attrappe, die Schreibvorgaenge mitzaehlt."""

        def __init__(self):
            self.gesetzt = []

        class _C:
            def __init__(self, aussen):
                self.aussen = aussen

            async def set(self, key, wert, ex=None):
                self.aussen.gesetzt.append((key, wert))

            async def get(self, key):
                return None

        @property
        def client(self):
            return SkillCrawlerBaumTests._Redis._C(self)

    def _dienst(self):
        from app.services.skill_crawler import SkillCrawlerService

        d = SkillCrawlerService.__new__(SkillCrawlerService)
        d.redis = self._Redis()
        return d

    def test_http_403_auf_den_baum_wird_gezaehlt(self):
        from app.services import skill_crawler as sc

        d = self._dienst()
        wache = Netzwache("Test")
        klient = _Klient([_Antwort(403, {}), _Antwort(403, {})])
        with patch.object(sc, "PAUSEN_HINTERGRUND_S", (0.0,)):
            skills = _lauf(d._crawl_github(klient, "owner/repo", wache=wache))

        self.assertEqual(skills, [])
        self.assertEqual(wache.anzahl, 0, "403 ist kein Verbindungsfehler")
        self.assertEqual(
            wache.abrufausfaelle, 1,
            "ein nicht geholter Baum muss gezaehlt werden, sonst rueckt der "
            "Frischestempel mit 0 Skills weiter (#977, Punkt 4)")

    def test_http_404_sperrt_den_katalog_nicht(self):
        # Vom Gegenlesen gefunden: eine umbenannte oder geloeschte Quelle in der
        # Liste ist Alltag. Zaehlte ihr 404 in die Stempel-Entscheidung, braeche
        # JEDER Lauf mit einer Netzausfall-Meldung ab, bis ein Administrator die
        # Zeile entfernt — bei null Netzfehlern.
        from app.services import skill_crawler as sc

        d = self._dienst()
        wache = Netzwache("Test")
        klient = _Klient([_Antwort(404, {}), _Antwort(404, {})])
        with patch.object(sc, "PAUSEN_HINTERGRUND_S", (0.0,)):
            skills = _lauf(d._crawl_github(klient, "owner/weg", wache=wache))

        self.assertEqual(skills, [])
        self.assertEqual(wache.quellen_weg, 1)
        self.assertEqual(wache.abrufausfaelle, 0, "404 darf den Stempel nicht aufhalten")
        self.assertFalse(sc.lauf_unvollstaendig(
            erzeugt=1, bisher=2,
            verbindungsausfaelle=wache.anzahl, weitere_ausfaelle=wache.abrufausfaelle,
        ), "eine dauerhaft verschwundene Quelle ist kein unvollstaendiger Lauf")
        # Fuer die Statusmeldung DIESER Quelle zaehlt sie trotzdem — sonst stuende
        # dort weiter "ok: 0 skills".
        self.assertEqual(wache.ausfaelle_gesamt, 1)

    def test_403_auf_alle_quellen_haelt_den_katalog_zurueck(self):
        # Gegenstueck zum 404-Fall, am ERGEBNIS von crawl() gemessen statt nur an
        # _crawl_github: ohne ``weitere_ausfaelle`` im Aufruf von
        # ``lauf_unvollstaendig`` bliebe der Fix hier wirkungslos und der Test
        # gruen — genau diese Mutation hat das Gegenlesen ueberlebt.
        from app.services import skill_crawler as sc

        d = self._dienst()
        d.get_catalog = AsyncMock(return_value={"skill_count": 5})
        d._sync_to_db = AsyncMock()
        d._update_source_status = AsyncMock()
        # Genau EINE DB-Quelle, keine Umgebungs-Repos: so ist die Zahl der Abrufe
        # vorhersagbar (zwei Baum-Versuche: main, master).
        d._load_db_sources = AsyncMock(return_value=[
            {"id": 1, "name": "q", "location": "owner/repo", "kind": "github",
             "ref": None, "subdir": None, "credential_encrypted": None,
             "trusted": False, "enabled": True, "created_by": "admin"},
        ])
        klient = _Klient([_Antwort(403, {})] * 2)
        with patch.object(sc, "PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch.object(sc.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch.object(sc, "_env_repos", lambda: []), \
             patch("app.core.vorlagen_skills.vorlagen_aktualisieren", AsyncMock()):
            with self.assertRaises(sc.NetzaussetzerAbbruch) as ctx:
                _lauf(d.crawl())
        self.assertIn("ohne Baum", str(ctx.exception))
        # Zweite Zusicherung: der Zwischenspeicher darf dabei NICHT geschrieben
        # worden sein. Ein Test, der nur die Ausnahme prueft, bleibt gruen,
        # waehrend der leere Katalog trotzdem landet.
        self.assertEqual(d.redis.gesetzt, [])

    def test_quelle_ohne_skills_bleibt_ok(self):
        # Gegenprobe zum neuen Quellenstatus: ein erreichbares Repo OHNE SKILL.md
        # ist in Ordnung und muss weiter "ok: 0 skills" bekommen. Ohne diesen Test
        # blieb ``>=`` statt ``>`` gruen — und damit waere JEDE leere Quelle als
        # "fehlgeschlagen" vermerkt worden.
        from app.services import skill_crawler as sc

        d = self._dienst()
        d.get_catalog = AsyncMock(return_value={"skill_count": 0})
        d._sync_to_db = AsyncMock()
        vermerke = []
        d._update_source_status = AsyncMock(
            side_effect=lambda sid, text: vermerke.append((sid, text)))
        d._load_db_sources = AsyncMock(return_value=[
            {"id": 1, "name": "q", "location": "owner/leer", "kind": "github",
             "ref": None, "subdir": None, "credential_encrypted": None,
             "trusted": False, "enabled": True, "created_by": "admin"},
        ])
        klient = _Klient([_Antwort(200, {"tree": []})])
        with patch.object(sc, "PAUSEN_HINTERGRUND_S", (0.0,)), \
             patch.object(sc.httpx, "AsyncClient", lambda *a, **k: klient), \
             patch.object(sc, "_env_repos", lambda: []), \
             patch("app.core.vorlagen_skills.vorlagen_aktualisieren", AsyncMock()):
            skills = _lauf(d.crawl())

        self.assertEqual(skills, [])
        self.assertEqual(vermerke, [(1, "ok: 0 skills")])

    def test_leerer_baum_ist_ein_echter_leerstand(self):
        # Gegenprobe: der Baum KAM an und ist wirklich leer. Das darf nicht als
        # Ausfall gelten, sonst blockiert ein skill-freies Repo den Katalog.
        from app.services import skill_crawler as sc

        d = self._dienst()
        wache = Netzwache("Test")
        klient = _Klient([_Antwort(200, {"tree": []})])
        with patch.object(sc, "PAUSEN_HINTERGRUND_S", (0.0,)):
            skills = _lauf(d._crawl_github(klient, "owner/repo", wache=wache))

        self.assertEqual(skills, [])
        self.assertEqual(wache.abrufausfaelle, 0)
        self.assertEqual(wache.anzahl, 0)


if __name__ == "__main__":
    unittest.main()
