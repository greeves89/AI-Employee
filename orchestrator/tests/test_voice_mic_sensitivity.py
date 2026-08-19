"""Mikrofon-Empfindlichkeit im Sprachmodus, einstellbar WAEHREND des Gespraechs.

Nutzerbericht vom 19.08.2026: „speech reagiert zu schnell auf Toene.... kann man
das einstellen?" — konnte man nicht.

Ursache: die Tonschleife schickte JEDEN Frame an die Engine, egal wie leise. Die
Sprecherwechsel-Erkennung von Nova Sonic sitzt im Modell bei AWS; sie bekam also
jedes Umgebungsgeraeusch zu hoeren und entschied selbst, darauf zu reagieren. Die
beiden Schwellen standen zudem fest im Quelltext (``rms > 0.025``, zwei Frames).

Der Regler wirkt bewusst OHNE Neuaufbau der Sitzung: das Rauschtor ist unser
Code in der Tonkette, kein Parameter der Engine. Ein Neuaufbau mitten im
Gespraech waere hier eine Unterbrechung ohne Gegenwert.
"""

import unittest
from pathlib import Path

QUELLE = (Path(__file__).resolve().parents[2]
          / "frontend/src/components/agents/voice-session.tsx").read_text()


class TheGateExistsTests(unittest.TestCase):
    def test_quiet_frames_no_longer_reach_the_engine(self):
        """Das war die Ursache: alles ging raus."""
        self.assertIn("const durchlassen =", QUELLE)
        self.assertIn("if (!durchlassen) ds.fill(0);", QUELLE)

    def test_silence_is_sent_instead_of_nothing(self):
        """Der Tonstrom muss lueckenlos bleiben, sonst geraet die
        Sprecherwechsel-Erkennung der Engine aus dem Takt. Also Stille senden,
        nicht Frames weglassen."""
        block = QUELLE.split("if (!durchlassen)", 1)[1][:120]
        self.assertIn("fill(0)", block)
        self.assertIn("wsRef.current.send", block)

    def test_word_endings_are_not_clipped(self):
        """Leise Endsilben liegen unter der Schwelle — ohne Nachlauf schneidet
        das Tor sie ab."""
        self.assertIn("NACHLAUF_FRAMES", QUELLE)
        self.assertIn("nachlauf > 0", QUELLE)


class ItIsAdjustableDuringTheCallTests(unittest.TestCase):
    def test_the_value_lives_in_a_ref_so_it_takes_effect_immediately(self):
        """In einem Zustand statt einem Ref wuerde die Tonschleife den alten
        Wert weiterbenutzen, bis sie neu aufgebaut wird."""
        self.assertIn("empfindlichkeitRef", QUELLE)
        self.assertIn("empfindlichkeitRef.current", QUELLE)

    def test_there_is_a_control_in_the_call_view(self):
        self.assertIn('aria-label="Mikrofon-Empfindlichkeit"', QUELLE)

    def test_no_reconnect_is_triggered_by_moving_it(self):
        """Ausdruecklich so gebaut: ein Neuaufbau waere hier eine
        Unterbrechung ohne Gegenwert."""
        block = QUELLE.split("useEffect(() => {\n    empfindlichkeitRef.current", 1)
        if len(block) == 2:
            self.assertNotIn("reconnect", block[1][:400].lower())

    def test_the_hardcoded_thresholds_are_gone(self):
        """Beide standen fest im Quelltext und waren der Grund, warum man
        nichts einstellen konnte."""
        self.assertNotIn("rms > 0.025", QUELLE)
        self.assertNotIn("vadHigh >= 2", QUELLE)

    def test_the_barge_in_follows_the_same_setting(self):
        """Sonst reagierte das Unterbrechen weiter nach der alten festen
        Schwelle, waehrend der Regler nur das Zuhoeren beeinflusst."""
        self.assertIn("pegel > schwelle ? vadHigh + 1 : 0", QUELLE)
        self.assertIn("vadHigh >= minFrames", QUELLE)


class TheSettingBelongsToTheDeviceTests(unittest.TestCase):
    def test_it_is_stored_locally_not_on_the_agent(self):
        """Mikrofon und Raum gehoeren zum Geraet, nicht zum Agenten — derselbe
        Agent an einem anderen Rechner braucht einen anderen Wert."""
        self.assertIn("localStorage.setItem('voice-empfindlichkeit'", QUELLE)
        self.assertIn("localStorage.getItem('voice-empfindlichkeit')", QUELLE)

    def test_zero_means_the_gate_is_off(self):
        """Wer es wie frueher haben will, soll das koennen — und der Regler
        sagt es auch."""
        self.assertIn("schwelle <= 0 || nachlauf > 0", QUELLE)
        self.assertIn("Rauschtor aus", QUELLE)


if __name__ == "__main__":
    unittest.main()
