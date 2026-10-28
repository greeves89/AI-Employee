"""Geltungsbereich und Pruefprotokoll der Browser-Arbeitsflaeche (#828, Schritt 5).

Ab diesem Schritt handelt der Agenten-Browser mit den Rechten des Nutzers:
Er ist in dessen Sitzungen angemeldet. Zwei Zusagen muessen deshalb
nachweisbar halten, und zwar fuer BEIDE Akteure -- das Modell mit einem
eigenen Werkzeugaufruf und den Nutzer, der in der Arbeitsflaeche eine
Adresse eintippt:

1. Eine gesetzte Freigabeliste SPERRT wirklich, und zwar BEVOR die Seite
   geladen wird. Ein Scope-Fehler, der erst nach ``goto`` auffaellt, hat die
   Anfrage schon hinausgelassen -- Verweis-Kopfzeile, Sitzungsmerkmale, alles.
2. Jeder Versuch steht im Pruefprotokoll, der gesperrte genauso wie der
   erlaubte, mit dem Akteur. Ein Protokoll, das nur die erlaubten Faelle
   kennt, beantwortet die eine Frage nicht, fuer die man es aufschlaegt.

Ohne echtes Chromium: die Seite ist ein Doppel, das jeden ``goto`` mitschreibt.
So faellt auch auf, wenn die Sperre nur den Rueckgabewert faelscht und trotzdem
laedt.
"""

import importlib
import os
import unittest
from unittest import mock


class SeitenDoppel:
    """Minimale Playwright-Seite -- schreibt jeden Ladeversuch mit."""

    def __init__(self):
        self.goto_aufrufe: list[str] = []
        self.url = "about:blank"

    async def goto(self, url, wait_until=None):
        self.goto_aufrufe.append(url)
        self.url = url

    async def title(self):
        return "Doppel"

    def is_closed(self):
        return False


class ScopeUndAudit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import app.tools.browser as browser
        import app.browser_stream as bs

        self.browser = importlib.reload(browser)
        self.bs = importlib.reload(bs)

        self.seite = SeitenDoppel()

        async def _seite():
            return self.seite

        async def _kontext_verboten():
            raise AssertionError("Chromium wurde gestartet, obwohl die Adresse "
                                 "gesperrt ist -- die Pruefung kommt zu spaet.")

        self.browser._ensure_page = _seite
        self.browser._ensure_context = _kontext_verboten
        # Profil-Sicherung ist hier nicht der Pruefgegenstand und wuerde auf
        # den echten Standardpfad schreiben.
        self.browser.profil_sichern = lambda: None

    def _mit_freigabe(self, wert: str):
        return mock.patch.dict(os.environ, {"BROWSER_ALLOWED_DOMAINS": wert})

    # --- 1. Host-Abgleich, nicht Zeichenkette ------------------------------

    def test_host_abgleich_statt_teilstring(self):
        f = self.browser._url_in_scope
        self.assertTrue(f("https://example.com/x", ["example.com"]))
        self.assertTrue(f("https://a.example.com/x", ["example.com"]))
        self.assertTrue(f("https://example.com", [".example.com"]))
        # Die drei Faelle, die ein Teilstring-Vergleich durchlassen wuerde:
        self.assertFalse(f("https://example.com.angreifer.tld/x", ["example.com"]))
        self.assertFalse(f("https://boeseexample.com/x", ["example.com"]))
        self.assertFalse(f("https://example.com@angreifer.tld/x", ["example.com"]))
        # Kein Host = keine Freigabe.
        self.assertFalse(f("nicht-mal-eine-url", ["example.com"]))
        self.assertFalse(f("", ["example.com"]))

    def test_mehrdeutiger_host_gilt_nicht_als_freigegeben(self):
        """Adressen, die Python und Browser verschieden lesen, sind gesperrt.

        Dieselbe Luecke wie in der Freigabeliste des Orchestrators (v1.381.1):
        ``urlparse`` liest ``https://fremd.example\\.erlaubt.example/`` als Host
        ``fremd.example\\.erlaubt.example`` -- das endet auf ``.erlaubt.example``
        und waere damit freigegeben. Chromium ruft aber ``fremd.example`` auf.
        Ein Host-Vergleich allein genuegt also NICHT; mehrdeutige Adressen
        muessen abgelehnt werden, sonst ist die Freigabeliste umgehbar.
        """
        f = self.browser._url_in_scope
        erlaubt = ["erlaubt.example"]
        # Was weiter gehen MUSS (sonst ist die Haertung zu breit):
        self.assertTrue(f("https://erlaubt.example/x", erlaubt))
        self.assertTrue(f("https://a.erlaubt.example/x", erlaubt))
        # Die drei Mehrdeutigkeits-Klassen:
        self.assertFalse(f("https://fremd.example\\.erlaubt.example/", erlaubt),
                         "Backslash im Host umgeht die Freigabeliste")
        self.assertFalse(f("https://fremd.example%2e.erlaubt.example/", erlaubt),
                         "Prozent-Kodierung im Host umgeht die Freigabeliste")
        self.assertFalse(f("https://fremd.example\t.erlaubt.example/", erlaubt),
                         "Steuerzeichen im Host umgeht die Freigabeliste")

    async def test_navigate_mit_backslash_umgehung_laedt_nicht(self):
        """Verhaltensnachweis: die Seite darf gar nicht geladen werden.

        Der Rueckgabewert allein beweist nichts -- erst ein leeres
        ``goto_aufrufe`` zeigt, dass die Anfrage den Container nie verlassen hat.
        """
        with self._mit_freigabe("erlaubt.example"):
            ergebnis = await self.browser.run(
                {"action": "navigate",
                 "url": "https://fremd.example\\.erlaubt.example/geheim"})

        self.assertTrue(ergebnis.startswith("Error:"), ergebnis)
        self.assertEqual([], self.seite.goto_aufrufe)
        eintraege = self.bs.audit_log()
        self.assertEqual(1, len(eintraege))
        self.assertFalse(eintraege[0]["erlaubt"])

    def test_ohne_freigabeliste_unbeschraenkt(self):
        with mock.patch.dict(os.environ, {"BROWSER_ALLOWED_DOMAINS": ""}):
            self.assertIsNone(self.browser._scope_fehler("https://irgendwo.invalid"))
        with self._mit_freigabe("example.com"):
            self.assertIsNone(self.browser._scope_fehler("https://example.com"))
            self.assertIsNotNone(self.browser._scope_fehler("https://irgendwo.invalid"))

    # --- 2. Die Sperre wirkt VOR dem Laden ---------------------------------

    async def test_navigate_gesperrt_laedt_nicht_und_nennt_den_grund(self):
        with self._mit_freigabe("example.com"):
            ergebnis = await self.browser.run(
                {"action": "navigate", "url": "https://angreifer.invalid/geheim"})

        self.assertTrue(ergebnis.startswith("Error:"), ergebnis)
        self.assertIn("angreifer.invalid", ergebnis)
        self.assertIn("example.com", ergebnis)       # nennt, was erlaubt waere
        self.assertEqual([], self.seite.goto_aufrufe)

        eintraege = self.bs.audit_log()
        self.assertEqual(1, len(eintraege))
        self.assertEqual("navigate", eintraege[0]["aktion"])
        self.assertFalse(eintraege[0]["erlaubt"])
        self.assertEqual("agent", eintraege[0]["akteur"])
        self.assertIn("angreifer.invalid", eintraege[0]["url"])

    async def test_new_tab_gesperrt_oeffnet_keinen_kontext(self):
        with self._mit_freigabe("example.com"):
            ergebnis = await self.browser.run(
                {"action": "new_tab", "url": "angreifer.invalid"})

        self.assertTrue(ergebnis.startswith("Error:"), ergebnis)
        eintraege = self.bs.audit_log()
        self.assertEqual(["new_tab"], [e["aktion"] for e in eintraege])
        self.assertFalse(eintraege[0]["erlaubt"])
        # Ohne Schema eingetippt -- protokolliert wird die Adresse, die
        # tatsaechlich geprueft wurde, nicht die Rohform.
        self.assertEqual("https://angreifer.invalid", eintraege[0]["url"])

    async def test_erlaubte_adresse_laedt_und_steht_im_protokoll(self):
        with self._mit_freigabe("example.com"):
            ergebnis = await self.browser.run(
                {"action": "navigate", "url": "https://a.example.com/start"})

        self.assertFalse(ergebnis.startswith("Error:"), ergebnis)
        self.assertEqual(["https://a.example.com/start"], self.seite.goto_aufrufe)
        eintraege = self.bs.audit_log()
        self.assertEqual(1, len(eintraege))
        self.assertTrue(eintraege[0]["erlaubt"])

    # --- 3. Der Akteur steht drin ------------------------------------------

    async def test_akteur_nutzer_wird_durchgereicht(self):
        with self._mit_freigabe("example.com"):
            await self.browser.run(
                {"action": "navigate", "url": "https://example.com/"}, akteur="nutzer")
            await self.browser.run({"action": "navigate", "url": "https://example.com/2"})

        self.assertEqual(["nutzer", "agent"],
                         [e["akteur"] for e in self.bs.audit_log()])

    async def test_arbeitsflaeche_meldet_die_sperre_statt_sie_zu_verschlucken(self):
        """Eine Sperre MUSS beim Nutzer ankommen.

        Vorher gab ``eingabe()`` den Rueckgabewert von ``browser.run`` weg:
        Der Nutzer tippte eine gesperrte Adresse ein, nichts passierte, und
        nichts sagte ihm warum.
        """
        strom = self.bs.BrowserStream()

        async def _sitzung():
            return object()

        strom._cdp_sitzung = _sitzung

        with self._mit_freigabe("example.com"):
            with self.assertRaises(ValueError) as fehler:
                await strom.eingabe({"art": "navigate", "url": "https://angreifer.invalid"})

        self.assertIn("angreifer.invalid", str(fehler.exception))
        self.assertEqual([], self.seite.goto_aufrufe)
        self.assertEqual("nutzer", self.bs.audit_log()[0]["akteur"])

    # --- 4. Das Protokoll ist abrufbar und begrenzt ------------------------

    def _anfrage(self, kopf: dict):
        anfrage = mock.Mock()
        anfrage.headers = kopf
        return anfrage

    async def test_audit_handler_gibt_das_protokoll_nur_dem_orchestrator(self):
        import json

        self.bs.audit_eintrag("navigate", "https://example.com", erlaubt=True,
                              akteur="agent")
        with mock.patch.dict(os.environ, {"BROWSER_ZUGANG": "geheim-123"}):
            ok = await self.bs.audit_handler(self._anfrage({"X-Browser-Zugang": "geheim-123"}))
            falsch = await self.bs.audit_handler(self._anfrage({"X-Browser-Zugang": "geraten"}))
            ohne = await self.bs.audit_handler(self._anfrage({}))

        daten = json.loads(ok.body.decode())
        self.assertEqual(1, len(daten["audit_log"]))
        self.assertEqual("https://example.com", daten["audit_log"][0]["url"])
        self.assertEqual(403, falsch.status)
        self.assertEqual(403, ohne.status)

    async def test_ohne_zugangsschluessel_bleibt_die_tuer_zu(self):
        """Container eines alten Orchestrators: leerer Schluessel darf nicht passen."""
        with mock.patch.dict(os.environ, {"BROWSER_ZUGANG": ""}):
            antwort = await self.bs.audit_handler(self._anfrage({"X-Browser-Zugang": ""}))
            strom = await self.bs.stream_handler(self._anfrage({}))
        self.assertEqual(403, antwort.status)
        self.assertEqual(403, strom.status)

    def test_protokoll_ohne_query_fragment_und_zugangsdaten(self):
        self.bs.audit_eintrag(
            "navigate", "https://nutzer:pw@login.example.com:8443/cb?code=GEHEIM#tok=X",
            erlaubt=True, akteur="agent")
        self.assertEqual("https://login.example.com:8443/cb", self.bs.audit_log()[-1]["url"])

    # --- Weiterleitungen und Navigation ausserhalb von ``navigate`` --------

    async def test_weiterleitung_nach_aussen_wird_geleert_und_gemeldet(self):
        class Umleitende(SeitenDoppel):
            async def goto(self, url, wait_until=None):
                self.goto_aufrufe.append(url)
                self.url = "https://angreifer.invalid/x" if url.startswith("https://example.com") else url

        self.seite = Umleitende()
        with self._mit_freigabe("example.com"):
            ergebnis = await self.browser.run(
                {"action": "navigate", "url": "https://example.com/redirect"})

        self.assertTrue(ergebnis.startswith("Error:"), ergebnis)
        self.assertIn("angreifer.invalid", ergebnis)
        self.assertEqual("about:blank", self.seite.url)

    async def test_wache_sperrt_navigation_des_hauptrahmens(self):
        """Klicks, Formulare und Eingaben der Arbeitsflaeche gehen an ``navigate`` vorbei."""
        kontext = mock.Mock()
        kontext.pages = []
        kontext.route = mock.AsyncMock()
        with self._mit_freigabe("example.com"):
            await self.browser._wache_anbringen(kontext)
            pruefen = kontext.route.call_args.args[1]

            def route_fuer(url, hauptrahmen=True, navigation=True):
                route = mock.Mock()
                route.abort = mock.AsyncMock()
                route.continue_ = mock.AsyncMock()
                route.request.url = url
                route.request.is_navigation_request.return_value = navigation
                route.request.frame.parent_frame = None if hauptrahmen else object()
                return route

            fremd = route_fuer("https://angreifer.invalid/")
            await pruefen(fremd)
            erlaubt = route_fuer("https://a.example.com/")
            await pruefen(erlaubt)
            rahmen = route_fuer("https://widget.invalid/", hauptrahmen=False)
            await pruefen(rahmen)
            bild = route_fuer("https://cdn.invalid/bild.png", navigation=False)
            await pruefen(bild)

        fremd.abort.assert_awaited_once()
        fremd.continue_.assert_not_awaited()
        for route in (erlaubt, rahmen, bild):
            route.continue_.assert_awaited_once()
            route.abort.assert_not_awaited()
        self.assertEqual("seite", self.bs.audit_log()[-1]["akteur"])

    async def test_ohne_freigabeliste_keine_wache(self):
        kontext = mock.Mock()
        kontext.route = mock.AsyncMock()
        with mock.patch.dict(os.environ, {"BROWSER_ALLOWED_DOMAINS": ""}):
            await self.browser._wache_anbringen(kontext)
        kontext.route.assert_not_awaited()

    def test_protokoll_waechst_nicht_unbegrenzt(self):
        for i in range(500):
            self.bs.audit_eintrag("navigate", f"https://example.com/{i}",
                                  erlaubt=True, akteur="agent")
        self.assertEqual(200, len(self.bs.audit_log()))
        # Es bleiben die JUENGSTEN Eintraege stehen.
        self.assertEqual("https://example.com/499", self.bs.audit_log()[-1]["url"])

    # --- 5. Weiche Schranke fuer den MCP-Browser ---------------------------

    def test_mcp_herkuenfte_nur_vollstaendig_und_nur_bei_gesetzter_liste(self):
        with mock.patch.dict(os.environ, {"BROWSER_ALLOWED_DOMAINS": ""}):
            self.assertEqual([], self.browser.mcp_erlaubte_herkuenfte())

        with self._mit_freigabe("example.com, a.example.org"):
            herkuenfte = self.browser.mcp_erlaubte_herkuenfte()

        self.assertEqual(
            ["https://example.com", "http://example.com",
             "https://a.example.org", "http://a.example.org"],
            herkuenfte)
        # Der MCP kennt keinen Unterdomaenen-Platzhalter; einen zu schicken
        # waere eine Zusage, die er nicht einhaelt.
        self.assertFalse(any("*" in h for h in herkuenfte))


if __name__ == "__main__":
    unittest.main()
