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
BROWSER_ARGS = ["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
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


def link_erlaubt(url: str) -> bool:
    """Nur echte Teams-Beitrittslinks — der Browser öffnet sonst, was in einer Nachricht steht."""
    teile = urlsplit(url or "")
    return teile.scheme == "https" and teile.hostname in ("teams.microsoft.com", "teams.live.com") \
        and teile.path.startswith(("/l/meetup-join/", "/meet/"))


def wav(pcm: bytes, rate: int = RATE) -> bytes:
    """PCM16 mono → WAV (für den Transkriptionsdienst)."""
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return puffer.getvalue()


def ist_stille(pcm: bytes, schwelle: int = 300) -> bool:
    """Nur Stille? Dann nicht transkribieren (kostet sonst je Stück einen Aufruf)."""
    if not pcm:
        return True
    import array

    werte = array.array("h", pcm[: len(pcm) - len(pcm) % 2])
    return max((abs(v) for v in werte), default=0) < schwelle


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
        await feld.first.fill(name, timeout=30000)
    except Exception:  # noqa: BLE001
        logger.info("[Teilnehmer] Kein Namensfeld (angemeldet oder andere Fassung)")
    await klick("Join now", "Jetzt teilnehmen", zeit=30000)


async def teilnehmen(meeting_id: str, join_url: str, name: str, ende: datetime) -> None:
    """Einem Termin beitreten, zuhören, Ton liefern, rechtzeitig wieder gehen."""
    from playwright.async_api import async_playwright

    sammler = Tonsammler()
    warteschlange: asyncio.Queue[bytes] = asyncio.Queue()
    bis = ende + NACHLAUF
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=chromium_pfad(), args=BROWSER_ARGS)
        try:
            ctx = await browser.new_context(locale="de-DE", permissions=["microphone"])
            page = await ctx.new_page()
            await page.expose_function("__aieHoeren", lambda b64: warteschlange.put_nowait(b64))
            await page.add_init_script(path=str(_BRUECKE))
            await page.goto(join_url + ("&" if "?" in join_url else "?") + "msLaunch=false&suppressPrompt=true",
                            wait_until="domcontentloaded", timeout=60000)
            await _beitreten(page, name)
            logger.info("[Teilnehmer] %s: Beitritt angefragt", meeting_id)
            import base64

            beitritt = datetime.now(timezone.utc)
            while datetime.now(timezone.utc) < bis:
                try:
                    b64 = await asyncio.wait_for(warteschlange.get(), timeout=5)
                except asyncio.TimeoutError:
                    b64 = None
                if b64:
                    stueck = sammler.hinzu(base64.b64decode(b64))
                    if stueck:
                        await _senden(meeting_id, stueck)
                if sammler.letzter_ton is None and datetime.now(timezone.utc) - beitritt > BEITRITT_WARTEN:
                    logger.warning("[Teilnehmer] %s: kein Ton nach %s — nicht hereingelassen?", meeting_id, BEITRITT_WARTEN)
                    break
                if page.is_closed():
                    break
            await _senden(meeting_id, sammler.rest())
        finally:
            await browser.close()
    logger.info("[Teilnehmer] %s: beendet", meeting_id)


def starten(nachricht: dict) -> bool:
    """Aus der Nachricht ``type=teilnehmen`` eine Teilnahme starten — höchstens eine je Termin."""
    meeting_id = str(nachricht.get("meeting_id") or "")
    join_url = str(nachricht.get("join_url") or "")
    if not meeting_id or not link_erlaubt(join_url):
        logger.warning("[Teilnehmer] Nachricht ohne gültigen Termin bzw. Teams-Link — verworfen")
        return False
    alt = _laufend.get(meeting_id)
    if alt is not None and not alt.done():
        return False
    try:
        ende = datetime.fromisoformat(str(nachricht.get("ende")))
    except ValueError:
        ende = datetime.now(timezone.utc) + timedelta(hours=1)
    if ende.tzinfo is None:
        ende = ende.replace(tzinfo=timezone.utc)
    name = str(nachricht.get("name") or "KI-Agent")[:60]

    async def lauf():
        try:
            await teilnehmen(meeting_id, join_url, name, ende)
        except Exception as e:  # noqa: BLE001 — der Agent selbst läuft weiter
            logger.warning("[Teilnehmer] %s abgebrochen: %s", meeting_id, e)

    _laufend[meeting_id] = asyncio.get_running_loop().create_task(lauf())
    return True
