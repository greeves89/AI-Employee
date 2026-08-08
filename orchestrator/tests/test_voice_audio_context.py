"""Ein angehaltener AudioContext ist stumm — ohne einen einzigen Fehler.

Chrome startet einen AudioContext ohne Nutzergeste als `suspended`. Auf der
AUFNAHME-Seite feuert `onaudioprocess` dann nie: die Verbindung steht, der Agent
begruesst, und danach kommt nichts mehr an. Auf der WIEDERGABE-Seite werden die
Bloecke brav eingeplant und nie hoerbar: in der Oberflaeche steht „Spricht…", aus dem
Lautsprecher kommt nichts.

Beides sah der Nutzer am 2026-08-08 hintereinander. Keiner der beiden Faelle erzeugt
eine Fehlermeldung — deshalb hier Wachtests statt Vertrauen.
"""

import unittest
from pathlib import Path

UI = (Path(__file__).resolve().parents[2]
      / "frontend/src/components/agents/voice-session.tsx").read_text()


class CaptureContextTests(unittest.TestCase):
    def test_the_input_context_is_resumed(self):
        live = UI.split("const startLive", 1)[1].split("const teardownRealtime", 1)[0]
        self.assertIn('if (ctx.state === "suspended")', live)
        self.assertIn("await ctx.resume()", live)

    def test_a_silent_capture_is_reported_instead_of_ignored(self):
        live = UI.split("const startLive", 1)[1].split("const teardownRealtime", 1)[0]
        self.assertIn("framesSent", live)
        self.assertIn("liefert keine Daten", live)
        self.assertIn("Audio-Kontext: ${ctx.state}", live)


class PlaybackContextTests(unittest.TestCase):
    def test_the_output_context_is_resumed_on_every_chunk(self):
        out = UI.split("const ensureOutCtx", 1)[1].split("const playPcmChunk", 1)[0]
        self.assertIn('outCtxRef.current.state === "suspended"', out)
        self.assertIn("resume()", out)


class ServerSideWarningTests(unittest.TestCase):
    """Und die Gegenprobe vom Server: hoert er 20 Sekunden nichts, sagt er es."""

    def test_the_session_reports_a_dead_microphone(self):
        src = (Path(__file__).resolve().parents[1]
               / "app/services/realtime_voice_session.py").read_text()
        self.assertIn("kein Signal von deinem Mikrofon", src)


if __name__ == "__main__":
    unittest.main()
