"""Der Agenten-Browser muss angemeldet bleiben und mehrere Tabs koennen.

Hintergrund (Issue #828): Der Agent soll Seiten im Auftrag des Nutzers
bedienen — der Nutzer meldet sich dabei selbst an, der Agent arbeitet in
DERSELBEN Sitzung weiter. Das ging bisher nicht: ``browser.py`` startete
``launch`` + ``new_context``, also jedes Mal ein frisches, abgemeldetes
Profil. Und es hielt genau EINE Seite, ein Ablauf ueber mehrere Seiten hinweg
war damit nicht abbildbar.

Diese Tests messen beides an einem echten Chromium nach:

* Sitzungsmerkmale (Cookie, lokaler Speicher) ueberleben das Schliessen und
  Wiederoeffnen des Browsers — das ist der eigentliche Kern.
* Tabs lassen sich oeffnen, auflisten, wechseln und schliessen, und der
  letzte Tab wird nie geschlossen (das wuerde die Sitzung kappen).

Bewusst ohne echtes Netz: Die Seiten werden von Playwright selbst
beantwortet (``context.route``). Ein Test, der an example.com haengt, faellt
sonst aus, sobald das Netz klemmt — und ein flatternder Test ist schlimmer als
keiner.

Ohne Playwright/Chromium wird uebersprungen.
"""

import asyncio
import importlib
import shutil
import tempfile
import unittest


def _playwright_da() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return False

    async def _probe():
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        try:
            b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            await b.close()
            return True
        finally:
            await pw.stop()

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


@unittest.skipUnless(_playwright_da(), "Playwright/Chromium nicht verfuegbar")
class BrowserProfilUndTabs(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.profil = tempfile.mkdtemp(prefix="browserprofil-")
        self.browser = self._modul_mit_profil(self.profil)

    async def asyncTearDown(self):
        await self.browser.close_browser()
        shutil.rmtree(self.profil, ignore_errors=True)

    async def _kontext(self):
        """Kontext bereitstellen und die Testseiten selbst beantworten."""
        context = await self.browser._ensure_context()
        if not getattr(context, "_geroutet", False):
            async def _antworten(route, request):
                await route.fulfill(
                    status=200, content_type="text/html",
                    body=f"<html><body><h1>Testseite</h1><p>{request.url}</p></body></html>",
                )
            await context.route("**/*", _antworten)
            context._geroutet = True
        return context

    @staticmethod
    def _modul_mit_profil(profil: str):
        """Modul frisch laden und auf ein Wegwerf-Profil zeigen lassen.

        Der Zustand (Kontext, Seiten) haengt an Modulvariablen — ein frischer
        Import ist deshalb die ehrlichste Nachbildung eines neuen Agentenlaufs.
        """
        import app.tools.browser as browser
        browser = importlib.reload(browser)
        browser.PROFIL_DIR = profil
        return browser

    # --- Der eigentliche Punkt --------------------------------------------

    async def test_anmeldung_ueberlebt_den_neustart(self):
        """Cookie und lokaler Speicher muessen den naechsten Lauf ueberstehen.

        Nachgebildet wird, was eine Anmeldung hinterlaesst: ein Cookie und ein
        Eintrag im lokalen Speicher. Ueberleben die nicht, ist der Agent beim
        naechsten Lauf abgemeldet — genau der Zustand von vorher.
        """
        context = await self._kontext()
        await self.browser.run({"action": "navigate", "url": "https://example.com"})
        await context.add_cookies([{
            "name": "sitzung", "value": "geheim-123",
            "domain": "example.com", "path": "/",
            # Ein Sitzungs-Cookie ohne Ablauf waere beim Schliessen weg; eine
            # echte Anmeldung setzt ein Ablaufdatum, also hier auch.
            "expires": 2 ** 31 - 1,
        }])
        seite = await self.browser._ensure_page()
        await seite.evaluate("localStorage.setItem('angemeldet', 'ja')")

        # Lauf beenden — wie am Ende eines Agentenlaufs.
        await self.browser.close_browser()

        # Neuer Lauf, gleiches Profil.
        self.browser = self._modul_mit_profil(self.profil)
        context = await self._kontext()
        await self.browser.run({"action": "navigate", "url": "https://example.com"})
        cookies = {c["name"]: c["value"] for c in await context.cookies("https://example.com")}
        self.assertEqual(
            cookies.get("sitzung"), "geheim-123",
            "Das Sitzungs-Cookie ist weg — der Agent waere beim naechsten Lauf "
            "wieder abgemeldet, und genau das soll das Profil verhindern.",
        )

        seite = await self.browser._ensure_page()
        self.assertEqual(
            await seite.evaluate("localStorage.getItem('angemeldet')"), "ja",
            "Der lokale Speicher ist weg; viele Anmeldungen haengen daran.",
        )

    async def test_liegengebliebene_sperre_blockiert_nicht(self):
        """Ein hart gestoppter Container hinterlaesst eine Sperrdatei.

        Passiert hier regelmaessig (Inaktivitaet, Speicherquote). Bleibt sie
        liegen, startet Chromium nie wieder mit diesem Profil.
        """
        import os
        await self._kontext()
        await self.browser.run({"action": "navigate", "url": "https://example.com"})
        await self.browser.close_browser()

        with open(os.path.join(self.profil, "SingletonLock"), "w") as f:
            f.write("tote-sperre")

        self.browser = self._modul_mit_profil(self.profil)
        ergebnis = await self.browser.run({"action": "navigate", "url": "https://example.com"})
        self.assertNotIn("Error", ergebnis, ergebnis)

    # --- Tabs --------------------------------------------------------------

    async def test_tabs_oeffnen_wechseln_schliessen(self):
        await self._kontext()
        await self.browser.run({"action": "navigate", "url": "https://example.com"})
        auf = await self.browser.run({"action": "new_tab", "url": "https://example.org"})
        self.assertIn("Opened tab", auf, auf)

        liste = await self.browser.run({"action": "list_tabs"})
        self.assertEqual(liste.count("\n") + 1, 2, f"Erwartet zwei Tabs:\n{liste}")
        self.assertIn("<- aktiv", liste)

        zurueck = await self.browser.run({"action": "switch_tab", "index": 0})
        self.assertIn("example.com", zurueck, zurueck)

        # Nach dem Wechsel muss auch das LESEN auf dem gewechselten Tab landen.
        text = await self.browser.run({"action": "read_text"})
        self.assertIn("example.com", text.splitlines()[0], text[:200])

        zu = await self.browser.run({"action": "close_tab", "index": 1})
        self.assertIn("Closed tab", zu, zu)
        self.assertEqual((await self.browser.run({"action": "list_tabs"})).count("\n") + 1, 1)

    async def test_der_letzte_tab_wird_nie_geschlossen(self):
        """Ihn zu schliessen wuerde den Kontext beenden und die Anmeldung kappen."""
        await self._kontext()
        await self.browser.run({"action": "navigate", "url": "https://example.com"})
        ergebnis = await self.browser.run({"action": "close_tab"})
        self.assertIn("Last tab kept", ergebnis, ergebnis)
        self.assertEqual((await self.browser.run({"action": "list_tabs"})).count("\n") + 1, 1)

    async def test_unbekannter_tab_meldet_die_vorhandenen(self):
        """Eine Fehlermeldung, mit der das Modell weiterarbeiten kann."""
        await self._kontext()
        await self.browser.run({"action": "navigate", "url": "https://example.com"})
        ergebnis = await self.browser.run({"action": "switch_tab", "index": 7})
        self.assertIn("no tab [7]", ergebnis)
        self.assertIn("[0]", ergebnis, "Die vorhandenen Tabs muessen mitgeliefert werden.")


class ChromiumPfad(unittest.TestCase):
    """Welches Chromium benutzt wird.

    Das Abbild bringt das System-Chromium mit; Playwrights Python-Paket sucht
    dagegen ein selbst heruntergeladenes, das dort nie ankommt. Am 21.09.2026
    stand deshalb beim ersten Livetest
    ``Executable doesn't exist at .../chrome-headless-shell`` -- und daraus
    folgte, dass dieses Werkzeug im Container noch nie gelaufen sein kann.

    Braucht kein Chromium, nur Dateien -- laeuft deshalb ohne Playwright.
    """

    def setUp(self):
        import importlib
        import app.tools.browser as browser
        self.browser = importlib.reload(browser)

    def _mit_umgebung(self, **werte):
        import os
        from unittest.mock import patch
        leer = {"BROWSER_EXECUTABLE": "", "PUPPETEER_EXECUTABLE_PATH": ""}
        return patch.dict(os.environ, {**leer, **werte}, clear=False)

    def test_gesetzter_pfad_wird_genommen(self):
        import os
        with tempfile.NamedTemporaryFile() as f:
            with self._mit_umgebung(BROWSER_EXECUTABLE=f.name):
                self.assertEqual(self.browser._chromium_pfad(), f.name)

    def test_puppeteer_pfad_gilt_als_rueckfall(self):
        """Den setzt das Abbild bereits -- er soll nicht zweimal gepflegt werden."""
        with tempfile.NamedTemporaryFile() as f:
            with self._mit_umgebung(PUPPETEER_EXECUTABLE_PATH=f.name):
                self.assertEqual(self.browser._chromium_pfad(), f.name)

    def test_ein_pfad_der_nicht_existiert_wird_ignoriert(self):
        """Sonst scheitert der Start mit einer schlechteren Meldung als noetig."""
        import os.path
        with self._mit_umgebung(BROWSER_EXECUTABLE="/gibt/es/nicht"):
            ergebnis = self.browser._chromium_pfad()
        self.assertNotEqual(ergebnis, "/gibt/es/nicht")
        if ergebnis is not None:
            self.assertTrue(os.path.exists(ergebnis))


if __name__ == "__main__":
    unittest.main()
