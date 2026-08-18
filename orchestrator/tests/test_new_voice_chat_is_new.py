"""Ein neu gestarteter Sprachchat darf nicht am alten Thema weitermachen.

Nutzerbericht vom 18.08.2026, mit Bildschirmfoto: „Wenn ich bei einem Agent
einen neuen Chat im Speech starte greift der noch immer auf den letzten zu."
Der frische Chat begruesste woertlich mit „Willkommen zurueck — wir waren
gerade dabei, deine E-Mails von heute anzuschauen."

Das war **Absicht**, nur zu weit gefasst: ist eine Sprachsitzung leer, laedt der
Server das letzte Gespraech des Agenten nach — „ein Kollege, den man zweimal
anruft, erinnert sich an das erste Telefonat". Fuer einen Verbindungsabbruch
und fuer den zweiten Anruf ist das genau richtig. Fuer ein ausdruecklich neu
gestartetes Gespraech ist es falsch.

Die Unterscheidung, die gefehlt hat: hat der NUTZER neu angefangen, oder ist die
Sitzung nur leer? Beides sah vorher gleich aus.
"""

import inspect
import unittest
from pathlib import Path

from app.services.realtime_voice_session import RealtimeVoiceSession

ROOT = Path(__file__).resolve().parents[2]
WS = (ROOT / "orchestrator/app/api/ws.py").read_text()
UI = (ROOT / "frontend/src/components/agents/voice-session.tsx").read_text()


class TheDistinctionExistsTests(unittest.TestCase):
    def test_the_session_knows_it_is_new(self):
        self.assertIn("neues_gespraech", RealtimeVoiceSession.__dataclass_fields__)

    def test_it_defaults_to_the_old_behaviour(self):
        """Ohne ausdrueckliches Signal bleibt das Nachladen — es ist nach einem
        Abbruch das Richtige."""
        self.assertIs(RealtimeVoiceSession.__dataclass_fields__["neues_gespraech"].default, False)


class ANewChatLoadsNothingTests(unittest.TestCase):
    SRC = inspect.getsource(RealtimeVoiceSession)

    def test_the_fallback_is_skipped_for_a_new_chat(self):
        self.assertIn("if not rows and not self.neues_gespraech:", self.SRC)

    def test_the_fallback_still_exists_for_a_resumed_call(self):
        """Es soll nicht abgeschafft, nur eingegrenzt werden."""
        self.assertIn("_resumed_from_earlier_call", self.SRC)
        self.assertIn("ChatMessage.session_id != self.session_id", self.SRC)

    def test_the_current_session_is_still_read(self):
        """Ein Wiederverbinden mitten im Gespraech muss den Faden behalten —
        das laeuft ueber die Zeilen DERSELBEN Sitzung und bleibt unberuehrt."""
        self.assertIn("ChatMessage.session_id == self.session_id", self.SRC)


class TheSignalTravelsFromTheBrowserTests(unittest.TestCase):
    def test_the_endpoint_accepts_it(self):
        self.assertIn("fresh: bool = Query(False)", WS)

    def test_it_reaches_the_session(self):
        self.assertIn('_rt_kwargs["neues_gespraech"] = True', WS)

    def test_the_browser_sends_it_for_a_new_chat(self):
        self.assertIn('"&fresh=1"', UI)

    def test_a_reconnect_does_not_send_it(self):
        """Sonst verlöre man beim Verbindungsabbruch den Gespraechsfaden — genau
        das, wogegen das Nachladen gebaut wurde."""
        self.assertIn("reconnectsRef.current === 0", UI)

    def test_resuming_a_named_session_does_not_send_it(self):
        self.assertIn("!resumeSessionId && reconnectsRef.current === 0", UI)


if __name__ == "__main__":
    unittest.main()
