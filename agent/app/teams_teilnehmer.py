"""Teams-Teilnehmer: Der Agent tritt einem Teams-Termin bei, zu dem er eingeladen ist, und hört zu.

Plan: docs/plaene/teams-teilnehmer.md. Der Orchestrator (services/teams_meetings, Rolle
„teilnehmer“) schickt zum Termin eine Nachricht ``type=teilnehmen`` mit dem Beitrittslink.

* Ein headless Chromium (Playwright, liegt im Abbild) öffnet den Link und tritt als
  „<Agent> (KI)“ bei. Wer den Termin führt, lässt ihn aus dem Wartebereich herein.
* Das Seitenskript ``teams_bruecke.js`` greift den Ton der anderen Teilnehmer ab
  (16 kHz PCM) und ersetzt das Mikrofon (für die Sprache, nächster Schritt).
* Alle ``STUECK_SEKUNDEN`` geht ein WAV-Stück an ``/meetings/{id}/teilnehmer-ton``. Der
  Orchestrator transkribiert es und hängt es an die Besprechungsseite. Daraus entsteht wie in
  Stufe 1 das Protokoll (ohne Werkzeuge).

Läuft als Aufgabe im Serverprozess (nicht als Kindprozess): Das Agenten-Token bleibt dort, wo
es ist (``serverwerte``), und kein Werkzeug erbt es. Netzwächter, Egress-Proxy und Zielliste
gelten, weil der Browser im Behälter des Agenten läuft.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

RATE = 16000
STUECK_SEKUNDEN = 30
#: Ohne Ton nach dem Beitritt: so lange warten, dann aufgeben (Wartebereich, falscher Link).
BEITRITT_WARTEN = timedelta(minutes=10)
#: Nach dem geplanten Ende höchstens so lange bleiben.
NACHLAUF = timedelta(minutes=15)
_BRUECKE = Path(__file__).with_name("teams_bruecke.js")
BROWSER_ARGS = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
                # WebRTC-Medien nicht per UDP am Egress-Proxy vorbei (Review 13.11.2026).
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
#: Höchstens so viele Besprechungen gleichzeitig je Agent.
HOECHSTENS_GLEICHZEITIG = 2
#: Länger bleibt ein Teilnehmer nie, egal was in der Nachricht steht.
HOECHSTDAUER = timedelta(hours=8)
#: Umgebung des Browsers: nur das Nötigste, keine Werte des Agenten-Servers.
_BROWSER_UMGEBUNG = ("HOME", "PATH", "LANG", "LC_ALL", "TZ", "PLAYWRIGHT_BROWSERS_PATH", "FONTCONFIG_PATH",
                     "XDG_RUNTIME_DIR")
_laufend: dict[str, asyncio.Task] = {}


def chromium_pfad() -> str | None:
    """Den Chromium im Abbild finden. Das npm-Playwright (Browser-MCP) bringt ihn mit; die
    Python-Bibliothek erwartet oft eine andere Build-Nummer. Ein zweiter Browser im Abbild
    wäre nur Ballast — also den vorhandenen nehmen, den neuesten."""
    import glob
    import os

    basis = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/ms-playwright")
    treffer = sorted(glob.glob(f"{basis}/chromium-*/chrome-linux*/chrome"),
                     key=lambda p: int("".join(c for c in p.split("chromium-")[1].split("/")[0] if c.isdigit()) or 0))
    return treffer[-1] if treffer else None


def teams_name(name: str) -> str:
    """Teams erlaubt im Namen nur Buchstaben, Zahlen, Leerzeichen und ``- ' . _ @`` —
    „Marketing (KI)“ ließe „Jetzt teilnehmen“ gesperrt (live gesehen 13.11.2026)."""
    import re

    sauber = re.sub(r"[^\w\s\-'.@]", " ", name or "", flags=re.UNICODE).replace("_", " ")
    sauber = re.sub(r"\s+", " ", sauber).strip()
    return (sauber or "KI-Agent")[:60]


def link_erlaubt(url: str) -> bool:
    """Nur echte Teams-Beitrittslinks — der Browser öffnet sonst, was in einer Nachricht steht.
    Kein Backslash, keine Anmeldedaten oder Ports, keine Steuerzeichen: Python und Chromium
    lesen ``https://evil.example\\@teams.microsoft.com/…`` verschieden (Review 13.11.2026).
    Gleiche Regel im Orchestrator (services/teams_meetings.teams_link_erlaubt)."""
    if not url or "\\" in url or "@" in url or any(ord(c) < 33 for c in url):
        return False
    teile = urlsplit(url)
    return (teile.scheme == "https" and teile.hostname in ("teams.microsoft.com", "teams.live.com")
            and teile.port is None and teile.username is None
            and teile.path.startswith(("/l/meetup-join/", "/meet/")))


#: Hauptrahmen darf nur hierhin navigieren (Teams und die Anmeldung von Microsoft).
ERLAUBTE_ZIELE = ("teams.microsoft.com", "teams.live.com", "microsoft.com", "microsoftonline.com",
                  "live.com", "office.com", "office.net", "skype.com", "teams.cdn.office.net")


def navigation_erlaubt(url: str) -> bool:
    teile = urlsplit(url or "")
    if teile.scheme in ("about", "blob", "data"):
        return True
    host = (teile.hostname or "").lower()
    return teile.scheme == "https" and any(host == z or host.endswith("." + z) for z in ERLAUBTE_ZIELE)


def wav(pcm: bytes, rate: int = RATE) -> bytes:
    """PCM16 mono → WAV (für den Transkriptionsdienst)."""
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return puffer.getvalue()


def ist_stille(pcm: bytes, schwelle: int = 150) -> bool:
    """Kaum Ton? Dann nicht transkribieren. Gemessen am mittleren Pegel (RMS), nicht am
    Spitzenwert: Ein einzelnes Knacken machte ein fast stilles Stück zu „Ton“, und Whisper
    erfand dazu Sätze („Thank you for joining us …“, live gesehen 13.11.2026)."""
    if not pcm:
        return True
    import array
    import math

    werte = array.array("h", pcm[: len(pcm) - len(pcm) % 2])
    if not werte:
        return True
    return math.sqrt(sum(v * v for v in werte) / len(werte)) < schwelle


class Tonsammler:
    """Sammelt 200-ms-Häppchen aus der Seite und gibt volle Stücke heraus."""

    def __init__(self, sekunden: int | None = None, rate: int = RATE):
        self.groesse = (sekunden or STUECK_SEKUNDEN) * rate * 2
        self.puffer = bytearray()
        self.letzter_ton: datetime | None = None

    def hinzu(self, pcm: bytes) -> bytes | None:
        self.puffer.extend(pcm)
        if not ist_stille(pcm):
            self.letzter_ton = datetime.now(timezone.utc)
        if len(self.puffer) >= self.groesse:
            stueck, self.puffer = bytes(self.puffer[: self.groesse]), self.puffer[self.groesse:]
            return stueck
        return None

    def rest(self) -> bytes:
        stueck, self.puffer = bytes(self.puffer), bytearray()
        return stueck


async def _senden(meeting_id: str, pcm: bytes) -> None:
    """Ein Stück an den Orchestrator. Stille wird gar nicht erst geschickt."""
    if ist_stille(pcm):
        return
    import httpx

    from app.config import settings

    url = f"{settings.orchestrator_url}/api/v1/meetings/{meeting_id}/teilnehmer-ton"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, params={"agent_id": settings.agent_id}, content=wav(pcm),
                                  headers={"X-Agent-Token": settings.agent_token, "Content-Type": "audio/wav"})
        if r.status_code >= 400:
            logger.warning("[Teilnehmer] Ton nicht angenommen: %s", r.status_code)
    except Exception as e:  # noqa: BLE001 — ein verlorenes Stück beendet die Teilnahme nicht
        logger.warning("[Teilnehmer] Ton nicht gesendet: %s", type(e).__name__)


async def _beitreten(page, name: str) -> None:
    """Teams im Browser: „Im Browser fortfahren“ → Name → „Jetzt teilnehmen“. Die Texte
    unterscheiden sich je Sprache; gesucht wird nach beiden. Fehlt ein Schritt (andere
    Fassung von Teams), geht es mit dem nächsten weiter."""
    async def klick(*texte: str, zeit: int = 15000) -> bool:
        for t in texte:
            try:
                await page.get_by_role("button", name=t).first.click(timeout=zeit)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    await klick("Continue on this browser", "In diesem Browser fortfahren", "Join on the web instead",
                "Stattdessen im Web teilnehmen", zeit=20000)
    try:
        feld = page.get_by_placeholder("Type your name").or_(page.get_by_placeholder("Geben Sie Ihren Namen ein"))
        await feld.first.fill(teams_name(name), timeout=30000)
    except Exception:  # noqa: BLE001
        logger.info("[Teilnehmer] Kein Namensfeld (angemeldet oder andere Fassung)")
    await klick("Join now", "Jetzt teilnehmen", zeit=30000)


async def teilnehmen(meeting_id: str, join_url: str, name: str, ende: datetime) -> None:
    """Einem Termin beitreten, zuhören, Ton liefern, rechtzeitig wieder gehen."""
    from playwright.async_api import async_playwright

    import os

    from app.tools.browser import proxy_einstellung

    sammler = Tonsammler()
    warteschlange: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)

    def hoeren(b64):
        try:
            warteschlange.put_nowait(b64)
        except asyncio.QueueFull:
            pass   # die Seite liefert schneller, als verarbeitet wird: verwerfen statt wachsen
    bis = ende + NACHLAUF
    async with async_playwright() as pw:
        zusatz = {}
        proxy = proxy_einstellung()
        if proxy:
            zusatz["proxy"] = proxy   # Chromium beachtet HTTPS_PROXY nicht von selbst
        browser = await pw.chromium.launch(
            headless=True, executable_path=chromium_pfad(), args=BROWSER_ARGS,
            env={k: os.environ[k] for k in _BROWSER_UMGEBUNG if k in os.environ}, **zusatz)
        try:
            # Service Worker sperren: Sie könnten Anfragen an der Prüfung unten vorbeiführen.
            ctx = await browser.new_context(locale="de-DE", permissions=["microphone"], service_workers="block")
            page = await ctx.new_page()
            await page.expose_function("__aieHoeren", hoeren)

            async def wache(route):
                anfrage = route.request
                if anfrage.is_navigation_request() and anfrage.frame == page.main_frame \
                        and not navigation_erlaubt(anfrage.url):
                    logger.warning("[Teilnehmer] Navigation gesperrt: %s", urlsplit(anfrage.url).hostname)
                    return await route.abort()
                await route.continue_()
            await ctx.route("**/*", wache)

            # Weiterleitungen sieht ``route`` nicht: nach jedem Wechsel des Hauptrahmens
            # nachprüfen und bei einem fremden Ziel die Teilnahme beenden. Popups gibt es nicht.
            def gewechselt(rahmen):
                if rahmen == page.main_frame and not navigation_erlaubt(rahmen.url):
                    logger.warning("[Teilnehmer] Seite außerhalb von Teams (%s) — beendet",
                                   urlsplit(rahmen.url).hostname)
                    asyncio.get_running_loop().create_task(page.close())
            page.on("framenavigated", gewechselt)
            ctx.on("page", lambda neu: None if neu == page else asyncio.get_running_loop().create_task(neu.close()))
            await page.add_init_script(path=str(_BRUECKE))
            await page.goto(join_url + ("&" if "?" in join_url else "?") + "msLaunch=false&suppressPrompt=true",
                            wait_until="domcontentloaded", timeout=60000)
            await _beitreten(page, name)
            logger.info("[Teilnehmer] %s: Beitritt angefragt", meeting_id)
            import base64

            stimme = Stimme(meeting_id, lambda b64: page.evaluate("b => window.__aieSprechen(b)", b64))
            if not await stimme.verbinden():
                stimme = None

            beitritt = datetime.now(timezone.utc)
            while datetime.now(timezone.utc) < bis:
                try:
                    b64 = await asyncio.wait_for(warteschlange.get(), timeout=5)
                except asyncio.TimeoutError:
                    b64 = None
                if b64:
                    if stimme is not None:
                        await stimme.ton(b64)
                    stueck = sammler.hinzu(base64.b64decode(b64))
                    if stueck:
                        await _senden(meeting_id, stueck)
                if sammler.letzter_ton is None and datetime.now(timezone.utc) - beitritt > BEITRITT_WARTEN:
                    logger.warning("[Teilnehmer] %s: kein Ton nach %s — nicht hereingelassen?", meeting_id, BEITRITT_WARTEN)
                    break
                if page.is_closed():
                    break
            await _senden(meeting_id, sammler.rest())
            if stimme is not None:
                await stimme.schliessen()
        finally:
            await browser.close()
    logger.info("[Teilnehmer] %s: beendet", meeting_id)


def stimme_url(orchestrator_url: str, agent_id: str, meeting_id: str) -> str:
    """WebSocket der Sprachfront für diesen Termin (orchestrator ``/agents/{id}/teams-stimme``)."""
    from urllib.parse import quote

    basis = orchestrator_url.rstrip("/")
    basis = "wss://" + basis[8:] if basis.startswith("https://") else "ws://" + basis.split("://", 1)[-1]
    return f"{basis}/api/v1/ws/agents/{quote(agent_id)}/teams-stimme?meeting_id={quote(meeting_id)}"


class Stimme:
    """Verbindung zur Sprachfront: Ton aus Teams hinein, Antwort (24 kHz PCM) zurück in die
    Besprechung. Ohne Echtzeit-Sprache (4009) oder bei Fehlern: der Teilnehmer hört weiter
    nur zu — die Stimme ist eine Zugabe, kein Muss."""

    def __init__(self, meeting_id: str, abspielen):
        self.meeting_id = meeting_id
        self.abspielen = abspielen            # async (b64 PCM 24 kHz) -> None
        self.ws = None
        self._sitzung = None
        self._empfang = None

    async def verbinden(self) -> bool:
        import aiohttp

        from app.config import settings

        try:
            self._sitzung = aiohttp.ClientSession()
            self.ws = await self._sitzung.ws_connect(
                stimme_url(settings.orchestrator_url, settings.agent_id, self.meeting_id),
                headers={"X-Agent-Token": settings.agent_token}, heartbeat=25, timeout=20)
        except Exception as e:  # noqa: BLE001
            logger.info("[Teilnehmer] keine Stimme (%s) — nur zuhören", type(e).__name__)
            await self.schliessen()
            return False
        self._empfang = asyncio.get_running_loop().create_task(self._empfangen())
        return True

    async def _empfangen(self) -> None:
        import json

        async for nachricht in self.ws:
            if nachricht.type != 1:            # aiohttp.WSMsgType.TEXT
                break
            try:
                ereignis = json.loads(nachricht.data)
            except ValueError:
                continue
            daten = ereignis.get("data") or {}
            if ereignis.get("type") == "audio_chunk" and daten.get("mime") == "audio/pcm" and daten.get("b64"):
                try:
                    await self.abspielen(daten["b64"])
                except Exception:  # noqa: BLE001
                    logger.debug("[Teilnehmer] Abspielen fehlgeschlagen", exc_info=True)
        logger.info("[Teilnehmer] Stimme getrennt (%s)", getattr(self.ws, "close_code", None))

    async def ton(self, b64: str) -> None:
        if self.ws is not None and not self.ws.closed:
            try:
                await self.ws.send_json({"type": "audio_chunk", "data": {"b64": b64}})
            except Exception:  # noqa: BLE001
                pass

    async def schliessen(self) -> None:
        if self._empfang is not None:
            self._empfang.cancel()
        if self.ws is not None:
            await self.ws.close()
        if self._sitzung is not None:
            await self._sitzung.close()


def starten(nachricht: dict) -> bool:
    """Aus der Nachricht ``type=teilnehmen`` eine Teilnahme starten — höchstens eine je Termin,
    höchstens ``HOECHSTENS_GLEICHZEITIG`` insgesamt, nie länger als ``HOECHSTDAUER``."""
    meeting_id = str(nachricht.get("meeting_id") or "")[:64]
    join_url = str(nachricht.get("join_url") or "")
    if not meeting_id or not link_erlaubt(join_url):
        logger.warning("[Teilnehmer] Nachricht ohne gültigen Termin bzw. Teams-Link — verworfen")
        return False
    for mid in [m for m, t in _laufend.items() if t.done()]:
        _laufend.pop(mid, None)
    if meeting_id in _laufend:
        return False
    if len(_laufend) >= HOECHSTENS_GLEICHZEITIG:
        logger.warning("[Teilnehmer] schon %d Besprechungen — keine weitere", len(_laufend))
        return False
    jetzt = datetime.now(timezone.utc)
    try:
        ende = datetime.fromisoformat(str(nachricht.get("ende")))
    except ValueError:
        ende = jetzt + timedelta(hours=1)
    if ende.tzinfo is None:
        ende = ende.replace(tzinfo=timezone.utc)
    ende = min(ende, jetzt + HOECHSTDAUER)
    name = str(nachricht.get("name") or "KI-Agent")[:60]

    async def lauf():
        try:
            await teilnehmen(meeting_id, join_url, name, ende)
        except Exception as e:  # noqa: BLE001 — der Agent selbst läuft weiter
            logger.warning("[Teilnehmer] %s abgebrochen: %s", meeting_id, e)

    aufgabe = asyncio.get_running_loop().create_task(lauf())
    _laufend[meeting_id] = aufgabe
    aufgabe.add_done_callback(lambda _t, m=meeting_id: _laufend.pop(m, None))
    return True
