"""Bildstrom und Eingaben fuer den Agenten-Browser (#828).

Der Agent bedient Seiten im Auftrag des Nutzers. Damit das ueberpruefbar und
uebernehmbar wird, braucht die Oberflaeche zwei Dinge: laufende Bilder der
Seite und einen Weg, Klicks und Tastatureingaben des Nutzers dorthin zu
bringen.

**Warum CDP-Screencast und nicht VNC:** ``Page.startScreencast`` liefert die
Bilder aus dem bereits laufenden Chromium. Kein X-Server, kein zweiter
Container, kein zusaetzlicher Dienst -- auf kleinen Anlagen ist das der
Unterschied zwischen "laeuft" und "laeuft nicht". Chromium liefert nur bei
tatsaechlicher Veraenderung ein Bild; eine ruhende Seite kostet also nichts.

**Warum der Rueckkanal nicht optional ist:** Der Anmeldeschritt gehoert dem
Nutzer. Tippt er sein Passwort direkt in die Seite, laeuft es weder durch den
Chat noch durch das Modell. Das geht nur, wenn die Ansicht Eingaben annimmt.

**Abgrenzung:** Das hier steuert den Browser IM Container. Der Bildschirm des
Nutzers laeuft weiterhin ueber die Computer-Use-Bridge.

Dies ist der Prototyp-Stand aus #828: noch ohne Geltungsbereiche,
Berechtigungen und Pruefprotokoll. Die Stelle, an der das anzuklemmen ist, ist
unten markiert.
"""

import asyncio
import base64
import json
import logging

from aiohttp import web

logger = logging.getLogger(__name__)

#: Bilder je Sekunde. Chromium schickt ohnehin nur bei Veraenderung; das hier
#: ist die Obergrenze, damit eine unruhige Seite nicht die Leitung flutet.
MAX_BILDER_PRO_SEKUNDE = 10

#: Bildqualitaet (JPEG). 60 ist der Punkt, an dem Text noch gut lesbar ist und
#: ein Bild rund 40-60 KB gross bleibt.
QUALITAET = 60

#: Obergrenze der Bildbreite. Groesser bringt in der Oberflaeche nichts und
#: kostet auf schmalen Leitungen spuerbar.
MAX_BREITE = 1280


class BrowserStream:
    """Haelt die CDP-Sitzung zur aktiven Seite und verteilt ihre Bilder."""

    def __init__(self):
        self._cdp = None
        self._seite = None
        self._zuschauer: set[web.WebSocketResponse] = set()
        self._letztes_bild: str | None = None
        self._lock = asyncio.Lock()

    # --- Aufbau und Abbau --------------------------------------------------

    async def _cdp_sitzung(self):
        """CDP-Sitzung zur AKTIVEN Seite -- auch nach einem Tabwechsel."""
        from app.tools import browser

        seite = await browser._ensure_page()
        if self._cdp is not None and self._seite is seite and not seite.is_closed():
            return self._cdp

        # Tab gewechselt oder erste Verbindung: alte Sitzung fallen lassen.
        await self._cdp_schliessen()

        self._seite = seite
        self._cdp = await seite.context.new_cdp_session(seite)
        self._cdp.on("Page.screencastFrame", self._bild_empfangen)
        await self._cdp.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": QUALITAET,
            "maxWidth": MAX_BREITE,
            "everyNthFrame": 1,
        })
        logger.info("[BrowserStream] Bildstrom gestartet fuer %s", seite.url)
        return self._cdp

    async def _cdp_schliessen(self) -> None:
        if self._cdp is None:
            return
        try:
            await self._cdp.send("Page.stopScreencast")
            await self._cdp.detach()
        except Exception:  # noqa: BLE001 — Seite kann schon zu sein
            logger.debug("[BrowserStream] CDP-Sitzung liess sich nicht sauber loesen",
                         exc_info=True)
        finally:
            self._cdp = None
            self._seite = None

    # --- Bilder ------------------------------------------------------------

    def _bild_empfangen(self, params: dict) -> None:
        """CDP-Rueckruf. Laeuft im Ereignisschleifen-Kontext, darf nicht blockieren."""
        daten = params.get("data")
        sitzung = params.get("sessionId")
        if not daten:
            return
        self._letztes_bild = daten
        asyncio.create_task(self._verteilen(daten))
        # Bestaetigen, sonst schickt Chromium kein weiteres Bild.
        if sitzung is not None and self._cdp is not None:
            asyncio.create_task(self._bestaetigen(sitzung))

    async def _bestaetigen(self, sitzung) -> None:
        try:
            await self._cdp.send("Page.screencastFrameAck", {"sessionId": sitzung})
        except Exception:  # noqa: BLE001
            logger.debug("[BrowserStream] Bestaetigung fehlgeschlagen", exc_info=True)

    async def _verteilen(self, daten: str) -> None:
        tot = []
        for ws in self._zuschauer:
            try:
                await ws.send_json({"typ": "bild", "daten": daten})
            except Exception:  # noqa: BLE001
                tot.append(ws)
        for ws in tot:
            self._zuschauer.discard(ws)

    # --- Eingaben ----------------------------------------------------------

    async def eingabe(self, ereignis: dict) -> None:
        """Ein Ereignis des Nutzers in die Seite geben.

        HIER kommen spaeter Geltungsbereich und Pruefprotokoll hin (#828):
        Vor dem Zustellen wird geprueft, ob die Sitzung diese Seite bedienen
        darf, und der Schritt wird protokolliert. Im Prototyp fehlt beides
        bewusst -- er laeuft nur an einem Agenten zum Anschauen.
        """
        cdp = await self._cdp_sitzung()
        art = ereignis.get("art")

        if art == "maus":
            await cdp.send("Input.dispatchMouseEvent", {
                "type": ereignis.get("typ", "mousePressed"),
                "x": float(ereignis.get("x", 0)),
                "y": float(ereignis.get("y", 0)),
                "button": ereignis.get("taste", "left"),
                "clickCount": int(ereignis.get("klicks", 1)),
            })
        elif art == "rad":
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": float(ereignis.get("x", 0)),
                "y": float(ereignis.get("y", 0)),
                "deltaX": float(ereignis.get("dx", 0)),
                "deltaY": float(ereignis.get("dy", 0)),
            })
        elif art == "taste":
            await cdp.send("Input.dispatchKeyEvent", {
                "type": ereignis.get("typ", "keyDown"),
                "key": ereignis.get("taste", ""),
                "code": ereignis.get("code", ""),
                "text": ereignis.get("text", ""),
            })
        elif art == "text":
            # Eingefuegter Text am Stueck -- Zeichen fuer Zeichen waere bei
            # einem langen Passwort quaelend langsam.
            await cdp.send("Input.insertText", {"text": ereignis.get("text", "")})
        else:
            raise ValueError(f"unbekannte Eingabeart: {art!r}")

    # --- Zuschauer ---------------------------------------------------------

    async def anmelden(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._zuschauer.add(ws)
            await self._cdp_sitzung()
        # Sofort etwas zeigen: Eine ruhende Seite schickt von sich aus kein
        # neues Bild, der Zuschauer saesse sonst vor Schwarz.
        if self._letztes_bild:
            await ws.send_json({"typ": "bild", "daten": self._letztes_bild})
        else:
            await self._bild_erzwingen(ws)

    async def _bild_erzwingen(self, ws: web.WebSocketResponse) -> None:
        try:
            from app.tools import browser
            seite = await browser._ensure_page()
            roh = await seite.screenshot(type="jpeg", quality=QUALITAET)
            await ws.send_json({"typ": "bild", "daten": base64.b64encode(roh).decode()})
        except Exception:  # noqa: BLE001
            logger.debug("[BrowserStream] Erstbild fehlgeschlagen", exc_info=True)

    async def abmelden(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._zuschauer.discard(ws)
            if not self._zuschauer:
                # Niemand schaut mehr zu -- Chromium soll nicht weiter Bilder
                # erzeugen, die niemand ansieht.
                await self._cdp_schliessen()


_strom = BrowserStream()


async def stream_handler(request: web.Request) -> web.WebSocketResponse:
    """WebSocket: Bilder hinaus, Eingaben hinein."""
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    try:
        await _strom.anmelden(ws)
    except Exception as e:  # noqa: BLE001
        # Benennen statt verschlucken: Sonst sieht die Oberflaeche nur eine
        # Verbindung, die sich sofort wieder schliesst.
        await ws.send_json({"typ": "fehler", "text": f"Browser nicht verfuegbar: {e}"})
        await ws.close()
        return ws

    try:
        async for nachricht in ws:
            if nachricht.type != web.WSMsgType.TEXT:
                continue
            try:
                await _strom.eingabe(json.loads(nachricht.data))
            except Exception as e:  # noqa: BLE001
                await ws.send_json({"typ": "fehler", "text": str(e)})
    finally:
        await _strom.abmelden(ws)
    return ws
