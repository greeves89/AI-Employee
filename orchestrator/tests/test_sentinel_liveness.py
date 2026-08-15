"""Wer bewacht den Waechter (#590 Punkt 6).

Ein Sentinel, der unbemerkt stehenbleibt, ist gefaehrlicher als gar keiner: die
Anlage sieht ueberwacht aus und ist es nicht. Genau davor warnt der Kommentar im
Dienst selbst — die Pruefung dazu fehlte bis jetzt.

Der Dienst legt alle 15 Sekunden ein Lebenszeichen in Redis ab, auch wenn nichts
zu tun ist. Das ist der Punkt: ein Sentinel, der stundenlang nichts sieht, ist
gesund; einer, der haengt, nicht. Ohne den Schlag in der Warteschleife saehen
beide gleich aus.

Der Wachhund im Zeitplaner liest den Zeitstempel. Ein FEHLENDES Lebenszeichen ist
bewusst kein Alarm — dann ist der Dienst schlicht ausgeschaltet, und das ist ein
gewollter Zustand.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.services.sentinel_service import (
    SENTINEL_HEARTBEAT_KEY,
    SentinelService,
)
from app.services.watchdog import is_sentinel_stale

JETZT = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class TheStalenessRuleTests(unittest.TestCase):
    def test_a_fresh_beat_is_fine(self):
        self.assertFalse(is_sentinel_stale(JETZT.timestamp() - 10, JETZT))

    def test_an_old_beat_is_stale(self):
        self.assertTrue(is_sentinel_stale(JETZT.timestamp() - 300, JETZT))

    def test_a_missing_beat_is_not_an_alert(self):
        """Kein Lebenszeichen heisst: der Dienst ist aus. Das ist eine
        Entscheidung des Betreibers, kein Stoerfall."""
        self.assertFalse(is_sentinel_stale(None, JETZT))
        self.assertFalse(is_sentinel_stale("", JETZT))

    def test_an_unreadable_beat_counts_as_silent(self):
        """Lieber ein Fehlalarm als ein blinder Fleck."""
        self.assertTrue(is_sentinel_stale("kaputt", JETZT))
        self.assertTrue(is_sentinel_stale(0, JETZT))

    def test_a_string_beat_is_accepted(self):
        """Redis liefert Zeichenketten, keine Fliesskommazahlen."""
        self.assertFalse(is_sentinel_stale(str(JETZT.timestamp() - 5), JETZT))

    def test_the_tolerance_spans_several_missed_beats(self):
        """15-Sekunden-Takt, zwei Minuten Schwelle: acht verpasste Schlaege.
        Genug fuer eine Redis-Neuverbindung, zu wenig fuer echten Stillstand."""
        self.assertFalse(is_sentinel_stale(JETZT.timestamp() - 100, JETZT))
        self.assertTrue(is_sentinel_stale(JETZT.timestamp() - 130, JETZT))

    def test_the_threshold_is_adjustable(self):
        self.assertTrue(
            is_sentinel_stale(JETZT.timestamp() - 20, JETZT, timedelta(seconds=10))
        )


class TheHeartbeatItselfTests(unittest.IsolatedAsyncioTestCase):
    def _dienst(self) -> SentinelService:
        redis = AsyncMock()
        redis.client = AsyncMock()
        return SentinelService(redis=redis, docker=None)

    async def test_it_writes_a_beat(self):
        s = self._dienst()
        await s._herzschlag()
        s.redis.client.set.assert_awaited_once()
        self.assertEqual(s.redis.client.set.await_args.args[0], SENTINEL_HEARTBEAT_KEY)

    async def test_it_does_not_write_on_every_pass(self):
        """Die Warteschleife laeuft im Sekundentakt — ein Schreibvorgang pro
        Durchlauf waere sinnlose Last auf Redis."""
        s = self._dienst()
        for _ in range(20):
            await s._herzschlag()
        self.assertEqual(s.redis.client.set.await_count, 1)

    async def test_a_redis_error_does_not_kill_the_watchman(self):
        """Ein Waechter, der wegen seines eigenen Lebenszeichens abstuerzt,
        waere absurd."""
        s = self._dienst()
        s.redis.client.set.side_effect = RuntimeError("Redis weg")
        await s._herzschlag()   # darf nicht werfen

    async def test_it_beats_even_when_nothing_happens(self):
        """Der eigentliche Punkt: der Schlag haengt an der Warteschleife, nicht
        am Ereignis. Sonst gaelte ein ruhiger Sentinel als toter."""
        import inspect

        src = inspect.getsource(SentinelService._consume)
        self.assertIn("await self._herzschlag()", src)


class TheWatchdogIsWiredTests(unittest.TestCase):
    """Eine Erkennung, die niemand aufruft, meldet nichts."""

    import pathlib

    SRC = (pathlib.Path(__file__).resolve().parents[1]
           / "app/services/scheduler_service.py").read_text()

    def test_the_scheduler_checks_it_every_tick(self):
        self.assertIn("await self._tick_sentinel_liveness()", self.SRC)

    def test_it_alerts_only_once(self):
        """Alle 30 Sekunden dieselbe Meldung waere Laerm, kein Alarm."""
        block = self.SRC.split("async def _tick_sentinel_liveness", 1)[1][:2400]
        self.assertIn("if self._sentinel_alerted:", block)
        self.assertIn("self._sentinel_alerted = False", block)

    def test_the_alert_says_what_it_means_for_the_operator(self):
        block = self.SRC.split("async def _tick_sentinel_liveness", 1)[1][:2400]
        self.assertIn("unbeaufsichtigt", block)

    def test_a_broken_check_does_not_kill_the_scheduler(self):
        self.assertIn("SentinelLiveness error", self.SRC)


if __name__ == "__main__":
    unittest.main()
