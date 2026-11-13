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
        boese = ["http://teams.microsoft.com/l/meetup-join/x", "https://teams.microsoft.com.evil.example/l/meetup-join/x",
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

    async def test_fremder_link_startet_nichts(self):
        self.assertFalse(tt.starten({"meeting_id": "m2", "join_url": "https://evil.example/l/meetup-join/x"}))
        self.assertNotIn("m2", tt._laufend)
