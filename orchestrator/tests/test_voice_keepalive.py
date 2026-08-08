"""Der Sprachstrom darf nicht sterben, weil das Mikrofon schweigt.

Nova Sonic bricht die bidirektionale Verbindung ab, wenn laenger als 55 Sekunden nichts
ankommt ("Timed out waiting for audio bytes or interactive content"). Die Erhaltungs-
schleife schickte Stille — aber erst, NACHDEM die Begruessung lief, und die lief erst nach
dem ersten echten Mikrofon-Frame. Kam vom Mikrofon nie etwas (Freigabe verweigert, falsches
Geraet, stummgeschaltet), blieb alles still: keine Begruessung, keine Stille, nach 55
Sekunden Abbruch. Der Nutzer sah nur eine AWS-Fehlermeldung.

Jetzt haelt die Schleife den Strom ab dem ersten Tick warm, die Begruessung spricht auch
ohne Zutun des Nutzers — und bleibt das Mikrofon stumm, wird das gesagt statt geraten.
"""

import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/services/realtime_voice_session.py").read_text()
LOOP = SRC.split("async def _keepalive_loop", 1)[1].split("\n    async def ", 1)[0]


class KeepaliveTests(unittest.TestCase):
    def test_it_no_longer_waits_for_the_greeting(self):
        self.assertNotIn("not self._greeted or not self._nova", LOOP)

    def test_silence_alone_can_trigger_the_greeting(self):
        self.assertIn("if not self._greeted:", LOOP)
        self.assertIn("asyncio.create_task(self._greet())", LOOP)

    def test_the_gap_stays_far_below_the_provider_limit(self):
        """55 Sekunden sind die Grenze — wir schicken alle paar Sekunden."""
        self.assertIn("_KEEPALIVE_IDLE_S = 5.0", SRC)
        self.assertIn("_KEEPALIVE_TICK_S = 2.0", SRC)


class DeadMicrophoneTests(unittest.TestCase):
    def test_real_audio_is_tracked_separately_from_silence(self):
        """Ohne diese Trennung sieht die Erhaltungs-Stille wie ein lebendiges Mikrofon aus."""
        self.assertIn("_last_real_audio", SRC)
        pump = SRC.split("async def _audio_pump", 1)[1].split("\n    # 16 kHz", 1)[0]
        self.assertIn("self._last_real_audio = self._last_audio_sent", pump)

    def test_the_user_is_told_instead_of_left_guessing(self):
        self.assertIn("kein Signal von deinem Mikrofon", LOOP)
        self.assertIn('"type": "status"', LOOP)

    def test_the_warning_comes_only_once(self):
        self.assertIn("_mic_warned", LOOP)
        self.assertIn("self._mic_warned = True", LOOP)


if __name__ == "__main__":
    unittest.main()
