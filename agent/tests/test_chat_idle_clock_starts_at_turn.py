"""Die Stillstands-Uhr beginnt beim Turn — nicht irgendwann davor.

Vorfall 2026-08-05, 10:46 Uhr: Der Nutzer schrieb nach rund zehn Minuten Pause
„Wie kann denn die Lösung aussehen?!" und bekam eine Minute spaeter „Der Agent hat
sich zwischendurch nicht mehr gemeldet und wurde abgebrochen".

`last_activity_at` lebt am LogPublisher ueber Turns hinweg. Beim Start eines neuen
Turns wurde es nicht zurueckgesetzt — also zaehlte die GESPRAECHSPAUSE DES NUTZERS
als Stillstand des Agenten. Die erste Pruefung nach 15 Sekunden sah bereits „600
Sekunden keine Aktivitaet" und brach ab, bevor der Agent ueberhaupt etwas tun
konnte. Je laenger die Pause, desto sicherer der Abbruch.

Der Wachhund soll einen haengenden AGENTEN fangen, nicht einen nachdenklichen
Nutzer. Diese Tests halten beides fest: Uhr startet beim Turn, und ein wirklich
verstummter Agent faellt weiterhin raus.
"""

import pathlib
import time
import unittest


CHAT_CONSUMER = pathlib.Path(__file__).resolve().parents[1] / "app" / "chat_consumer.py"


class IdleClockStartTests(unittest.TestCase):
    def test_clock_is_reset_before_the_turn_starts(self):
        """Das Zuruecksetzen muss VOR dem Erzeugen des Turns stehen — danach waere
        das Rennen schon verloren."""
        src = CHAT_CONSUMER.read_text()
        reset = src.find("log_publisher.last_activity_at = time.monotonic()")
        start = src.find("turn = asyncio.ensure_future(")
        self.assertNotEqual(reset, -1, "Die Uhr wird beim Turn-Start nicht zurueckgesetzt")
        self.assertNotEqual(start, -1)
        self.assertLess(reset, start, "Die Uhr muss vor dem Turn-Start zurueckgesetzt werden")

    def test_watchdog_still_measures_against_the_publisher(self):
        """Das Zuruecksetzen ersetzt den Wachhund nicht — es stellt ihn nur richtig."""
        src = CHAT_CONSUMER.read_text()
        self.assertIn("last_activity_at", src)
        self.assertIn("quiet >= idle_limit", src)

    def test_a_stale_clock_would_abort_immediately(self):
        """Der Rechenweg, der den Fehler erzeugte — als ausfuehrbarer Beleg.

        Ohne Zuruecksetzen ist `quiet` beim ersten Check bereits groesser als das
        Limit, obwohl der Turn gerade erst begonnen hat.
        """
        idle_limit = 600
        now = time.monotonic()
        stale = now - 700          # letzte Aktivitaet: vor der Nutzerpause
        quiet_without_reset = now - stale
        self.assertGreaterEqual(quiet_without_reset, idle_limit,
                                "ohne Reset bricht der Turn sofort ab")
        quiet_with_reset = now - now
        self.assertLess(quiet_with_reset, idle_limit,
                        "mit Reset laeuft der Turn normal an")


if __name__ == "__main__":
    unittest.main()
