"""Teams-Teilnehmer im Agenten (app/teams_teilnehmer.py) — alles außer dem echten Beitritt.

Der Beitritt selbst braucht einen echten Teams-Termin (docs/plaene/teams-teilnehmer.md). Die
Audio-Brücke ist im Spike belegt (docs/plaene/teams-teilnehmer-spike/)."""

import asyncio
import io
import struct
import unittest
import wave
from unittest.mock import patch

from app import teams_teilnehmer as tt


class LinkTests(unittest.TestCase):
    def test_nur_echte_teams_links(self):
        gut = ["https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%7d",
               "https://teams.microsoft.com/meet/123456789?p=abc", "https://teams.live.com/meet/9876"]
        boese = ["https://evil.example\\@teams.microsoft.com/l/meetup-join/x",
                 "https://user:pw@teams.microsoft.com/l/meetup-join/x", "https://teams.microsoft.com:8443/meet/1",
                 "https://teams.microsoft.com/meet/1 x", "https://teams.microsoft.com/meet/1\n",
                 "http://teams.microsoft.com/l/meetup-join/x", "https://teams.microsoft.com.evil.example/l/meetup-join/x",
                 "https://evil.example/l/meetup-join/x", "https://teams.microsoft.com/_#/settings",
                 "javascript:alert(1)", "", "file:///etc/passwd"]
        for u in gut:
            self.assertTrue(tt.link_erlaubt(u), u)
        for u in boese:
            self.assertFalse(tt.link_erlaubt(u), u)


class ChromiumTests(unittest.TestCase):
    def test_neuester_vorhandener_chromium(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for nr in ("1208", "1254", "999"):
                os.makedirs(f"{d}/chromium-{nr}/chrome-linux")
                open(f"{d}/chromium-{nr}/chrome-linux/chrome", "w").close()
            os.makedirs(f"{d}/chromium_headless_shell-1300/chrome-linux")
            with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": d}):
                self.assertEqual(tt.chromium_pfad(), f"{d}/chromium-1254/chrome-linux/chrome")
            with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": d + "/leer"}):
                self.assertIsNone(tt.chromium_pfad())


class NameTests(unittest.TestCase):
    def test_teams_erlaubt_keine_klammern(self):
        """Live 13.11.2026: „Marketing (KI)“ ließ „Jetzt teilnehmen“ gesperrt."""
        self.assertEqual(tt.teams_name("Marketing (KI)"), "Marketing KI")
        self.assertEqual(tt.teams_name("Marketing - KI"), "Marketing - KI")
        self.assertEqual(tt.teams_name("Anna's Bot @ Büro"), "Anna's Bot @ Büro")
        self.assertEqual(tt.teams_name("<script>"), "script")
        self.assertEqual(tt.teams_name("()"), "KI-Agent")


class NavigationTests(unittest.TestCase):
    def test_nur_teams_und_microsoft(self):
        for u in ("https://teams.microsoft.com/v2/", "https://login.microsoftonline.com/x",
                  "https://statics.teams.cdn.office.net/a.js", "about:blank"):
            self.assertTrue(tt.navigation_erlaubt(u), u)
        for u in ("https://evil.example/", "http://teams.microsoft.com/", "https://microsoft.com.evil.example/",
                  "https://notmicrosoft.com/", "file:///etc/passwd", "javascript:alert(1)"):
            self.assertFalse(tt.navigation_erlaubt(u), u)


class TonTests(unittest.TestCase):
    def _ton(self, sekunden: float, pegel: int) -> bytes:
        n = int(sekunden * tt.RATE)
        return b"".join(struct.pack("<h", pegel if i % 2 else -pegel) for i in range(n))

    def test_wav_ist_gueltig(self):
        pcm = self._ton(0.5, 1000)
        with wave.open(io.BytesIO(tt.wav(pcm))) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 16000))
            self.assertEqual(w.readframes(w.getnframes()), pcm)

    def test_stille(self):
        self.assertTrue(tt.ist_stille(b""))
        self.assertTrue(tt.ist_stille(self._ton(0.2, 50)))
        self.assertFalse(tt.ist_stille(self._ton(0.2, 4000)))

    def test_einzelnes_knacken_ist_stille(self):
        """Live: ein Stück mit RMS 19 galt als Ton — Whisper erfand dazu „Bye-bye“."""
        pcm = bytearray(self._ton(1, 5))
        pcm[100:102] = struct.pack("<h", 8000)
        self.assertTrue(tt.ist_stille(bytes(pcm)))

    def test_sammler_gibt_volle_stuecke(self):
        s = tt.Tonsammler(sekunden=1)
        haeppchen = self._ton(0.2, 3000)
        raus = [s.hinzu(haeppchen) for _ in range(12)]
        volle = [r for r in raus if r]
        self.assertEqual(len(volle), 2)
        self.assertTrue(all(len(v) == tt.RATE * 2 for v in volle))
        self.assertEqual(len(s.rest()), len(haeppchen) * 12 - 2 * tt.RATE * 2)
        self.assertIsNotNone(s.letzter_ton)

    def test_stille_wird_nicht_gesendet(self):
        with patch("httpx.AsyncClient") as client:
            asyncio.run(tt._senden("m1", self._ton(1, 10)))
        client.assert_not_called()


class StimmeTests(unittest.IsolatedAsyncioTestCase):
    def test_url(self):
        self.assertEqual(tt.stimme_url("http://ai-employee-orchestrator:8000", "a1", "m 1"),
                         "ws://ai-employee-orchestrator:8000/api/v1/ws/agents/a1/teams-stimme?meeting_id=m%201")
        self.assertTrue(tt.stimme_url("https://x.example.com/", "a1", "m1").startswith("wss://x.example.com/api/v1/ws/"))

    async def test_antwort_wird_abgespielt_fremdes_ignoriert(self):
        import json
        gespielt, gesendet = [], []

        class Msg:
            def __init__(self, daten): self.type, self.data = 1, json.dumps(daten)

        class WS:
            closed, close_code = False, 1000
            def __init__(self, ereignisse): self._e = ereignisse
            def __aiter__(self):
                async def gen():
                    for e in self._e:
                        yield Msg(e)
                return gen()
            async def send_json(self, d): gesendet.append(d)
            async def close(self): self.closed = True

        async def abspielen(b64): gespielt.append(b64)
        st = tt.Stimme("m1", abspielen)
        st.ws = WS([{"type": "audio_chunk", "data": {"mime": "audio/pcm", "b64": "QUJD", "rate": 24000}},
                    {"type": "audio_chunk", "data": {"mime": "audio/mpeg", "b64": "WFla"}},
                    {"type": "transcript", "data": {"text": "hallo"}}])
        await st._empfangen()
        self.assertEqual(gespielt, ["QUJD"], "nur PCM der Echtzeit-Sprache wird abgespielt")
        await st.ton("AAAA")
        self.assertEqual(gesendet, [{"type": "audio_chunk", "data": {"b64": "AAAA"}}])

    async def test_ohne_verbindung_nur_zuhoeren(self):
        st = tt.Stimme("m1", None)
        with patch("aiohttp.ClientSession.ws_connect", side_effect=ConnectionError("4009")):
            self.assertFalse(await st.verbinden())
        await st.ton("AAAA")   # darf nichts tun, nicht werfen


class StartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tt._laufend.clear()

    async def test_hoechstens_eine_teilnahme_je_termin(self):
        gestartet = []

        async def teilnehmen(*a):
            gestartet.append(a)
            await asyncio.sleep(0.2)
        n = {"meeting_id": "m1", "join_url": "https://teams.microsoft.com/l/meetup-join/abc",
             "name": "Assistenz (KI)", "ende": "2026-11-13T15:00:00+00:00"}
        with patch.object(tt, "teilnehmen", teilnehmen):
            self.assertTrue(tt.starten(n))
            self.assertFalse(tt.starten(n), "zweite Nachricht zum selben Termin startet nichts")
            await asyncio.sleep(0.05)
        self.assertEqual(len(gestartet), 1)
        self.assertEqual(gestartet[0][2], "Assistenz (KI)")

    async def test_hoechstens_zwei_gleichzeitig_und_hoechstdauer(self):
        laeufe = []

        async def teilnehmen(mid, url, name, ende):
            laeufe.append(ende)
            await asyncio.sleep(0.2)
        basis = {"join_url": "https://teams.microsoft.com/meet/1", "ende": "2099-01-01T00:00:00+00:00"}
        with patch.object(tt, "teilnehmen", teilnehmen):
            self.assertTrue(tt.starten({**basis, "meeting_id": "a"}))
            self.assertTrue(tt.starten({**basis, "meeting_id": "b"}))
            self.assertFalse(tt.starten({**basis, "meeting_id": "c"}), "dritte gleichzeitig abgelehnt")
            await asyncio.sleep(0.05)
            from datetime import datetime, timezone
            self.assertTrue(all(e <= datetime.now(timezone.utc) + tt.HOECHSTDAUER for e in laeufe))
            await asyncio.sleep(0.3)
            self.assertEqual(tt._laufend, {}, "beendete Teilnahmen werden aufgeräumt")
            self.assertTrue(tt.starten({**basis, "meeting_id": "c"}))
            await asyncio.sleep(0.3)

    async def test_fremder_link_startet_nichts(self):
        self.assertFalse(tt.starten({"meeting_id": "m2", "join_url": "https://evil.example/l/meetup-join/x"}))
        self.assertNotIn("m2", tt._laufend)
