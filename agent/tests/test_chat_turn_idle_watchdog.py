"""Ein arbeitender Agent darf nicht abgeschossen werden, nur weil es lange dauert.

Meldung 2026-08-04: Beim Umbau einer App ("Design ueberarbeiten, mobiltauglich
machen") lief der Agent, rief zwoelf Werkzeuge auf — und wurde dann mit "Die
Antwort hat zu lange gedauert" abgebrochen. Alles verworfen.

Ursache: eine feste Gesamtdauer pro Turn (claude_code 600s, codex 1800s), die
nicht danach fragt, ob der Agent noch arbeitet. Jetzt zaehlt der STILLSTAND: jedes
veroeffentlichte Ereignis setzt die Uhr zurueck. Ein wirklich haengender Turn faellt
weiterhin raus, ein langer aber lebendiger nicht.
"""

import inspect
import unittest
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[1] / "app" / "chat_consumer.py").read_text()
_PUB = (Path(__file__).resolve().parents[1] / "app" / "log_publisher.py").read_text()


class IdleWatchdogTests(unittest.TestCase):
    def test_publisher_records_a_heartbeat(self):
        """Ohne Lebenszeichen gibt es nichts, woran man Fortschritt erkennt."""
        self.assertIn("self.last_activity_at = time.monotonic()", _PUB)

    def test_heartbeat_is_set_on_every_chat_event(self):
        start = _PUB.index("async def publish_chat")
        body = _PUB[start:start + 1200]
        self.assertIn("last_activity_at", body)

    def test_consumer_measures_silence_not_total_duration(self):
        self.assertIn("last_activity_at", _SRC)
        self.assertIn("idle_limit", _SRC)

    def test_turn_is_shielded_from_the_poll_timeout(self):
        """Ohne shield wuerde der Turn schon beim ersten Zwischencheck sterben."""
        self.assertIn("asyncio.shield(turn)", _SRC)

    def test_stuck_turn_is_still_cancelled(self):
        """Der Wachhund darf nicht ganz entfallen — sonst blockiert ein haengender
        Turn die Warteschlange fuer immer."""
        self.assertIn("turn.cancel()", _SRC)

    def test_message_names_the_real_reason(self):
        """'Hat zu lange gedauert' war irrefuehrend — es ging nie um die Dauer."""
        self.assertIn("nicht mehr gemeldet", _SRC)
        self.assertNotIn("Die Antwort hat zu lange gedauert", _SRC)


if __name__ == "__main__":
    unittest.main()
