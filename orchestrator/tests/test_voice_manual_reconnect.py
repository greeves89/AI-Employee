"""Bei einem Sprachfehler muss „Neu verbinden" angeboten werden, nicht nur
„Auflegen".

Nutzerbericht vom 19.08.2026, mit Bildschirmfoto der Meldung
„Invalid input request, please fix your input and try again.":
„bei sowas will ich ein reconnect button statt auflegen".

Hintergrund: der Client baut von selbst neu auf, aber NUR bei Fehlern, die als
voruebergehend gelten (``_VORUEBERGEHEND``: Zeitueberschreitung, Drosselung,
abgerissener Stream …). ``invalid input request`` steht dort bewusst nicht —
eine Eingabepruefung blind zu wiederholen wuerde einen echten Fehler verdecken
und in eine Schleife laufen. Genau deshalb braucht es den HAENDISCHEN Knopf:
der Nutzer entscheidet, nicht die Automatik.

Der Aufbau selbst ist derselbe wie beim automatischen Wiederverbinden — er laedt
das bisherige Gespraech nach, der Agent redet also weiter statt neu zu
begruessen.
"""

import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
ANSICHT = (WURZEL / "frontend/src/components/agents/voice-session.tsx").read_text()
SITZUNG = (WURZEL / "orchestrator/app/services/realtime_voice_session.py").read_text()
NOVA = (WURZEL / "orchestrator/app/services/voice_providers/realtime_nova_sonic.py").read_text()


class TheButtonIsOfferedTests(unittest.TestCase):
    def test_there_is_a_reconnect_button_on_an_error(self):
        self.assertIn("Neu verbinden", ANSICHT)

    def test_it_sits_where_the_error_is_shown(self):
        block = ANSICHT.split("{error && (", 1)
        self.assertEqual(len(block), 2, "Fehleranzeige nicht gefunden")
        self.assertIn("neuVerbindenRef.current?.()", block[1][:900])

    def test_the_user_is_told_the_conversation_continues(self):
        """Sonst traut sich niemand zu druecken, aus Angst, das Gespraech zu
        verlieren."""
        self.assertIn("Das Gespräch wird fortgesetzt", ANSICHT)


class ItContinuesInsteadOfStartingOverTests(unittest.TestCase):
    def test_the_reconnect_does_not_ask_for_a_fresh_session(self):
        """``frisch`` haengt an ``reconnectsRef.current === 0``. Der Knopf setzt
        den Zaehler bewusst auf 1 — sonst begruesste der Agent neu, statt
        weiterzureden."""
        block = ANSICHT.split("neuVerbindenRef.current = () => {", 1)
        self.assertEqual(len(block), 2, "kein haendischer Wiederaufbau")
        self.assertIn("reconnectsRef.current = 1", block[1][:600])

    def test_the_old_socket_is_closed_first(self):
        block = ANSICHT.split("neuVerbindenRef.current = () => {", 1)[1][:600]
        self.assertIn("wsRef.current?.close()", block)

    def test_the_error_is_cleared_so_the_view_does_not_lie(self):
        block = ANSICHT.split("neuVerbindenRef.current = () => {", 1)[1][:600]
        self.assertIn("setError(null)", block)
        self.assertIn('setState("connecting")', block)

    def test_the_server_side_resume_still_exists(self):
        """Der Knopf nuetzt nichts, wenn der Server das Gespraech nicht
        nachlaedt."""
        self.assertIn("_resume_summary", SITZUNG)


class AValidationErrorIsNotRetriedBlindlyTests(unittest.TestCase):
    def test_it_is_not_in_the_transient_list(self):
        """Eine Eingabepruefung zu wiederholen wuerde denselben Fehler
        wiederholen — und den echten Grund verdecken."""
        block = SITZUNG.split("_VORUEBERGEHEND = (", 1)[1][:500]
        self.assertNotIn("invalid input", block.lower())

    def test_the_timeout_case_still_reconnects_by_itself(self):
        """Das war die Korrektur vom Vortag und muss so bleiben."""
        block = SITZUNG.split("_VORUEBERGEHEND = (", 1)[1][:500]
        self.assertIn("timed out", block)


class TheNextErrorSaysMoreTests(unittest.TestCase):
    """Zweimal am 19.08.2026 aufgetreten, und im Log stand nur die Meldung —
    nicht, worauf sie folgte. Ohne diese Spur bleibt die Ursache Raterei."""

    def test_the_last_sent_event_is_remembered(self):
        self.assertIn("letztes_ereignis", NOVA)

    def test_audio_frames_are_left_out_of_the_breadcrumb(self):
        """Sonst waere es jede Zehntelsekunde eine Zeile — und die
        interessante Spur waere ueberschrieben."""
        block = NOVA.split("art = next(iter(event.keys())", 1)[1][:300]
        self.assertIn('art != "audioInput"', block)

    def test_the_error_log_names_it(self):
        self.assertIn("zuletzt gesendet=%s", NOVA)


if __name__ == "__main__":
    unittest.main()
