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

    async def test_audit_handler_gibt_das_protokoll_heraus(self):
        import json

        self.bs.audit_eintrag("navigate", "https://example.com", erlaubt=True,
                              akteur="agent")
        antwort = await self.bs.audit_handler(None)
        daten = json.loads(antwort.body.decode())
        self.assertEqual(1, len(daten["audit_log"]))
        self.assertEqual("https://example.com", daten["audit_log"][0]["url"])

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
