"""Eine Fertigmeldung muss auch wirklich gesprochen werden.

Nutzerbericht vom 18.08.2026: Aufgabe per Sprache erteilt, Fokus-Modus an
(„Mikro aus — ich arbeite weiter und melde mich, wenn etwas fertig ist"), die
Aufgabe lief durch und stand in der Oberflaeche auf ERLEDIGT — **gesprochen
wurde nichts**.

Der Mechanismus war richtig gedacht: eine Zwischenmeldung wartet auf eine
Sprechpause, weil sie sonst an den laufenden Satz angehaengt und nie
ausgesprochen wird. Nur brach er nach 25 Sekunden ab und spielte die Meldung
**trotzdem** ein — also genau in die laufende Ausgabe, die er vermeiden sollte.

Warum das kein seltener Fall ist: ``_last_spoken`` wird bei JEDEM
Audioschnipsel neu gesetzt. Redet das Modell durchgehend, wird es nie still.
Im Protokoll der gemeldeten Sitzung reihte sich die Sprachausgabe von 11:49:26
bis 11:50:04 fast lueckenlos aneinander — 38 Sekunden am Stueck.

Jetzt wird weitergewartet statt die Meldung zu verheizen, und jeder Ausgang
steht im Protokoll. Vorher war die Funktion stumm: sie meldete nur im
Fehlerfall, und das auf Debug-Ebene. Deshalb war der Ausfall nicht
nachvollziehbar.
"""

import time
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from app.services.realtime_voice_session import RealtimeVoiceSession


class _Sitzung:
    """Nur die Teile, die ``_inject_when_quiet`` anfasst."""

    _inject_when_quiet = RealtimeVoiceSession._inject_when_quiet
    _engine_safe = staticmethod(RealtimeVoiceSession._engine_safe)
    NACHREICH_FRIST = 2.0          # im Test kurz, sonst dauert er Minuten

    def __init__(self, *, spricht_bis: float = 0.0):
        self.agent_id = "a1"
        self._closed = False
        self._drop_audio = False
        self._nova = AsyncMock()
        self._spricht_bis = time.monotonic() + spricht_bis
        self._last_spoken = 0.0

    @property
    def _last_spoken(self):
        # Solange „gesprochen" wird, ist der Zeitstempel immer frisch — genau
        # wie im Betrieb, wo ihn jeder Audioschnipsel neu setzt.
        return time.monotonic() if time.monotonic() < self._spricht_bis else self._still_seit

    @_last_spoken.setter
    def _last_spoken(self, wert):
        self._still_seit = wert


class ItWaitsForARealPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_quiet_session_gets_it_immediately(self):
        s = _Sitzung()
        self.assertTrue(await s._inject_when_quiet("Task fertig", timeout=1.0))
        s._nova.inject_user_text.assert_awaited_once()

    async def test_it_does_not_give_up_after_the_short_deadline(self):
        """Der gemeldete Fall: das Modell redet laenger als die kurze Frist.
        Frueher wurde hier mitten hinein eingespielt."""
        s = _Sitzung(spricht_bis=0.8)
        gestartet = time.monotonic()
        self.assertTrue(await s._inject_when_quiet("Task fertig", timeout=0.3))
        # Es wurde gewartet, bis die Rede vorbei war — nicht nach 0,3 s gefeuert.
        self.assertGreater(time.monotonic() - gestartet, 0.7)
        s._nova.inject_user_text.assert_awaited_once()

    async def test_endless_speech_still_gets_the_message_out(self):
        """Lieber einspielen und riskieren, dass es verschluckt wird, als eine
        fertige Aufgabe gar nicht zu melden."""
        s = _Sitzung(spricht_bis=999)
        s.NACHREICH_FRIST = 0.5
        self.assertTrue(await s._inject_when_quiet("Task fertig", timeout=0.2))
        s._nova.inject_user_text.assert_awaited_once()

    async def test_a_closed_session_drops_it(self):
        s = _Sitzung()
        s._closed = True
        self.assertFalse(await s._inject_when_quiet("Task fertig", timeout=0.2))
        s._nova.inject_user_text.assert_not_awaited()

    async def test_an_interrupted_turn_is_not_a_pause(self):
        """Waehrend ``_drop_audio`` laeuft, wird gerade eine Ausgabe verworfen —
        da hineinzureden ergibt nichts."""
        s = _Sitzung()
        s._drop_audio = True
        s.NACHREICH_FRIST = 0.4
        await s._inject_when_quiet("Task fertig", timeout=0.2)
        # Sie geht am Ende trotzdem raus, aber erst nach dem Warten.
        s._nova.inject_user_text.assert_awaited_once()


class EveryOutcomeIsInTheLogTests(unittest.IsolatedAsyncioTestCase):
    """Der Ausfall war nicht nachvollziehbar, weil die Funktion schwieg."""

    async def test_success_is_logged(self):
        s = _Sitzung()
        with self.assertLogs("app.services.realtime_voice_session", level="INFO") as protokoll:
            await s._inject_when_quiet("Task fertig", timeout=0.2)
        self.assertTrue(any("eingespielt" in z for z in protokoll.output))

    async def test_forcing_it_into_speech_is_a_warning(self):
        """Der Fall, der die Meldung verschluckt — der darf nicht leise sein."""
        s = _Sitzung(spricht_bis=999)
        s.NACHREICH_FRIST = 0.4
        with self.assertLogs("app.services.realtime_voice_session", level="WARNING") as protokoll:
            await s._inject_when_quiet("Task fertig", timeout=0.2)
        self.assertTrue(any("verschluckt" in z for z in protokoll.output))

    async def test_a_dropped_message_is_logged(self):
        s = _Sitzung()
        s._closed = True
        with self.assertLogs("app.services.realtime_voice_session", level="INFO") as protokoll:
            await s._inject_when_quiet("Task fertig", timeout=0.2)
        self.assertTrue(any("verworfen" in z for z in protokoll.output))


class TheCompletionPathUsesItTests(unittest.TestCase):
    """Eine Wartefunktion, die niemand aufruft, meldet nichts."""

    import inspect
    SRC = inspect.getsource(RealtimeVoiceSession)

    def test_a_finished_task_is_announced_through_it(self):
        self.assertIn("_inject_when_quiet(note)", self.SRC)

    def test_the_notice_tells_the_agent_to_speak_now(self):
        """Ohne ausdrueckliche Aufforderung nimmt das Modell die Meldung nur zur
        Kenntnis und sagt nichts."""
        quelle = (Path(__file__).resolve().parents[2]
                  / "orchestrator/app/services/realtime_voice_session.py").read_text()
        block = quelle.split("FERTIG. Ergebnis", 1)[1][:500]
        self.assertIn("JETZT", block)


if __name__ == "__main__":
    unittest.main()
