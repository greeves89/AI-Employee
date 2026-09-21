"""Bildstrom und Eingaben des Agenten-Browsers (#828).

Zwei Dinge muessen nachweisbar funktionieren, sonst ist die Arbeitsflaeche
wertlos:

1. Es kommen **Bilder** an -- auch bei einer ruhenden Seite. Chromium schickt
   von sich aus nur bei Veraenderung; wer eine stehende Seite oeffnet, saehe
   sonst Schwarz und hielte die Verbindung fuer kaputt.
2. Eingaben des Nutzers landen **wirklich in der Seite**. Daran haengt der
   eigentliche Zweck: Der Nutzer meldet sich selbst an, damit Passwoerter
   weder durch den Chat noch durch das Modell laufen. Ein Rueckkanal, der nur
   scheinbar funktioniert, waere schlimmer als keiner.

Gegen ein echtes Chromium, ohne Netzzugriff (die Seite kommt aus dem Test).
"""

import asyncio
import base64
import importlib
import shutil
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

SEITE = """
<html><body style="margin:0">
  <button id="knopf" style="position:absolute;left:100px;top:100px;width:200px;height:80px">
    Klick mich
  </button>
  <input id="feld" style="position:absolute;left:100px;top:300px;width:300px;height:40px">
  <div id="ergebnis">nichts</div>
  <script>
    document.getElementById('knopf').onclick =
      () => document.getElementById('ergebnis').textContent = 'geklickt';
  </script>
</body></html>
"""


def _playwright_da() -> bool:
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
class BrowserStreamTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.profil = tempfile.mkdtemp(prefix="stromprofil-")

        import app.tools.browser as browser
        self.browser = importlib.reload(browser)
        self.browser.PROFIL_DIR = self.profil

        import app.browser_stream as bs
        self.bs = importlib.reload(bs)

        # Testseite selbst ausliefern -- kein Netz noetig.
        context = await self.browser._ensure_context()
        async def _antworten(route, _request):
            await route.fulfill(status=200, content_type="text/html", body=SEITE)
        await context.route("**/*", _antworten)
        await self.browser.run({"action": "navigate", "url": "https://example.com"})

        app = web.Application()
        app.router.add_get("/browser/stream", self.bs.stream_handler)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.browser.close_browser()
        shutil.rmtree(self.profil, ignore_errors=True)

    async def _bild_abwarten(self, ws, sekunden=10):
        async with asyncio.timeout(sekunden):
            while True:
                nachricht = await ws.receive_json()
                if nachricht.get("typ") == "bild":
                    return nachricht["daten"]
                if nachricht.get("typ") == "fehler":
                    self.fail(f"Strom meldet Fehler: {nachricht['text']}")

    # --- Bilder ------------------------------------------------------------

    async def test_ruhende_seite_liefert_trotzdem_sofort_ein_bild(self):
        async with self.client.ws_connect("/browser/stream") as ws:
            daten = await self._bild_abwarten(ws)
        roh = base64.b64decode(daten)
        self.assertGreater(len(roh), 1000, "Das Bild ist verdaechtig klein.")
        self.assertEqual(roh[:2], b"\xff\xd8", "Kein JPEG — Chromium liefert etwas anderes.")

    async def test_veraenderung_erzeugt_ein_neues_bild(self):
        async with self.client.ws_connect("/browser/stream") as ws:
            await self._bild_abwarten(ws)
            seite = await self.browser._ensure_page()
            await seite.evaluate("document.body.style.background = 'red'")
            zweites = await self._bild_abwarten(ws)
        self.assertGreater(len(zweites), 1000)

    # --- Eingaben ----------------------------------------------------------

    async def test_ein_klick_des_nutzers_landet_in_der_seite(self):
        seite = await self.browser._ensure_page()
        async with self.client.ws_connect("/browser/stream") as ws:
            await self._bild_abwarten(ws)
            for typ in ("mousePressed", "mouseReleased"):
                await ws.send_json({"art": "maus", "typ": typ,
                                    "x": 200, "y": 140, "taste": "left", "klicks": 1})
            await seite.wait_for_function(
                "document.getElementById('ergebnis').textContent === 'geklickt'",
                timeout=5000,
            )
        self.assertEqual(await seite.inner_text("#ergebnis"), "geklickt")

    async def test_getippter_text_landet_im_feld(self):
        """Der Fall, um den es geht: Der Nutzer tippt selbst, nicht das Modell."""
        seite = await self.browser._ensure_page()
        await seite.focus("#feld")
        async with self.client.ws_connect("/browser/stream") as ws:
            await self._bild_abwarten(ws)
            await ws.send_json({"art": "text", "text": "geheim"})
            await seite.wait_for_function(
                "document.getElementById('feld').value === 'geheim'", timeout=5000)
        self.assertEqual(await seite.input_value("#feld"), "geheim")

    async def test_unbekannte_eingabe_wird_benannt_statt_verschluckt(self):
        async with self.client.ws_connect("/browser/stream") as ws:
            await self._bild_abwarten(ws)
            await ws.send_json({"art": "quatsch"})
            async with asyncio.timeout(5):
                while True:
                    nachricht = await ws.receive_json()
                    if nachricht.get("typ") == "fehler":
                        self.assertIn("quatsch", nachricht["text"])
                        return

    # --- Aufraeumen --------------------------------------------------------

    async def test_ohne_zuschauer_wird_der_bildstrom_beendet(self):
        """Sonst erzeugt Chromium dauerhaft Bilder, die niemand ansieht."""
        async with self.client.ws_connect("/browser/stream") as ws:
            await self._bild_abwarten(ws)
            self.assertIsNotNone(self.bs._strom._cdp)
        for _ in range(50):
            if self.bs._strom._cdp is None:
                break
            await asyncio.sleep(0.1)
        self.assertIsNone(self.bs._strom._cdp,
                          "CDP-Sitzung blieb offen, obwohl niemand mehr zusieht.")


if __name__ == "__main__":
    unittest.main()
