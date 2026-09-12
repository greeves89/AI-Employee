"""Ein Lebenszeichen ist nur so viel wert wie das, was es bezeugt.

Vorfall 2026-09-11, Lauf ``tccxfej99``: 344 Minuten belegter Aufgabenplatz,
davon 18,4 Minuten echte Arbeit. Die restlichen 5h26 war der Platz belegt,
ohne dass irgendjemand arbeitete — beendet erst durch einen fremden Neustart
des Orchestrators (#730).

Ursache: der Herzschlag war ein unbedingtes ``while True: sleep(60); publish()``.
Er bezeugte damit, dass die COROUTINE lebt — nicht, dass ARBEIT stattfindet. Eine
Coroutine, die in einem haengenden Werkzeugaufruf klemmt, lebt prima. Der Waechter
im Orchestrator mass also eine Groesse, die im Klemmfall per Konstruktion nie
altert: die Ueberwachung war fail-open, und kein Schwellwert der Welt haette das
geheilt.

Diese Tests laufen gegen das ECHTE Verhalten (die Coroutine wird gestartet), nicht
gegen den Quelltext. Gegen den alten unbedingten Herzschlag muessen sie ROT sein:
``test_herzschlag_verstummt_ohne_fortschritt`` und
``test_stillstand_wird_je_aufgabe_gemessen`` warten dort ewig auf ein Ende, das
nie kommt.
"""

import asyncio
import time
import unittest
from unittest.mock import patch

from app import task_consumer as tc
from app.log_publisher import LogPublisher
from app.task_consumer import TaskConsumer

#: Die Tests laufen gegen die ECHTE Uhr. ``time.monotonic`` zu stellen ist hier
#: keine Option: ``log_publisher`` importiert das Modul, ein Patch trifft also den
#: Prozess — samt der Uhr, aus der die Ereignisschleife ihre Fristen rechnet, und
#: ``asyncio.sleep`` kehrt dann nie zurueck.
#: Stattdessen wird die Grenze klein gesetzt und wirklich gewartet.
GRENZE = 0.5
#: Herzschlagtakt — deutlich kleiner als die Grenze, damit mehrere Schlaege fallen.
TAKT = 0.02
#: Abstand, in dem „Arbeit" gemeldet wird. Erst ein 50-facher Einbruch der
#: Maschine koennte daraus faelschlich einen Stillstand machen.
ARBEITSTAKT = 0.01


class FakeRedis:
    def __init__(self):
        self.veroeffentlicht: list[tuple[str, str]] = []

    async def publish(self, channel, message):
        self.veroeffentlicht.append((channel, message))

    async def rpush(self, *a, **k):
        pass

    async def ltrim(self, *a, **k):
        pass

    async def hset(self, *a, **k):
        pass


class HerzschlagTestfall(unittest.TestCase):
    def _aufbau(self) -> tuple[TaskConsumer, FakeRedis, LogPublisher]:
        redis = FakeRedis()
        verbraucher = TaskConsumer("agent-test")
        verbraucher.redis = redis
        verbraucher._log_publisher = LogPublisher(redis, "agent-test")
        verbraucher.HERZSCHLAG_SEKUNDEN = TAKT
        return verbraucher, redis, verbraucher._log_publisher

    @staticmethod
    def _schlaege(redis: FakeRedis) -> int:
        return sum(1 for kanal, _ in redis.veroeffentlicht if kanal == "task:heartbeat")

    def _lauf(self, korpus):
        with patch.object(tc, "_stillstand_grenze", return_value=GRENZE):
            return asyncio.run(korpus)


class HerzschlagBezeugtArbeit(HerzschlagTestfall):
    def test_herzschlag_schlaegt_solange_fortschritt_kommt(self):
        """Gegenrichtung zu #692: ein langer, aber lebendiger Lauf darf NICHT sterben.

        Am 31.08.2026 starben vier kerngesunde Reviews nach 30,3 Minuten, weil der
        Waechter die blosse Dauer mass. Diese Absicherung darf der #730-Umbau nicht
        wieder einreissen.
        """
        async def korpus():
            verbraucher, redis, veroeffentlicher = self._aufbau()
            veroeffentlicher.notiere_fortschritt("t1")
            schlag = asyncio.create_task(verbraucher._herzschlag("t1"))
            # Bewusst LAENGER als die Grenze laufen lassen: sonst wuerde der Test
            # auch dann gruen, wenn der Herzschlag den Stillstand nie prueft.
            ende = time.monotonic() + GRENZE * 2
            while time.monotonic() < ende:
                await asyncio.sleep(ARBEITSTAKT)
                veroeffentlicher.notiere_fortschritt("t1")
            laeuft_noch = not schlag.done()
            schlag.cancel()
            return laeuft_noch, self._schlaege(redis)

        laeuft_noch, schlaege = self._lauf(korpus())
        self.assertTrue(laeuft_noch, "Herzschlag verstummt trotz laufender Arbeit")
        self.assertGreater(schlaege, 0, "Kein einziges Lebenszeichen bei echter Arbeit")

    def test_herzschlag_verstummt_ohne_fortschritt(self):
        """DER Kern von #730: ohne Fortschritt muss das Lebenszeichen AUFHOEREN.

        Gegen den alten unbedingten Herzschlag laeuft dieser Test in den Timeout —
        genau das ist die Gegenprobe.
        """
        async def korpus():
            verbraucher, redis, veroeffentlicher = self._aufbau()
            # Ab hier passiert nie wieder etwas — die Aufgabe klemmt.
            veroeffentlicher.notiere_fortschritt("t1")
            begonnen = time.monotonic()
            await asyncio.wait_for(verbraucher._herzschlag("t1"), timeout=10)
            verstummt_nach = time.monotonic() - begonnen
            # Nach dem Ende darf schlicht nichts mehr nachkommen.
            vorher = self._schlaege(redis)
            await asyncio.sleep(TAKT * 5)
            return verstummt_nach, vorher, self._schlaege(redis)

        verstummt_nach, vorher, nachher = self._lauf(korpus())
        self.assertLess(
            verstummt_nach, GRENZE * 3,
            "Eine klemmende Aufgabe sendet weiter Lebenszeichen — der Platz bliebe "
            "erneut stundenlang belegt (#730)",
        )
        self.assertEqual(vorher, nachher, "Der Herzschlag schlaegt nach dem Ende weiter")

    def test_stillstand_wird_je_aufgabe_gemessen(self):
        """Alle Aufgaben teilen sich EINEN LogPublisher.

        Mit einem gemeinsamen Zeitstempel wuerde eine fleissige Aufgabe den
        Herzschlag einer laengst klemmenden am Leben halten — dieselbe Blindstelle,
        nur eine Ebene hoeher.
        """
        async def korpus():
            verbraucher, redis, veroeffentlicher = self._aufbau()
            veroeffentlicher.notiere_fortschritt("klemmt")
            veroeffentlicher.notiere_fortschritt("fleissig")
            fleissig = asyncio.create_task(verbraucher._herzschlag("fleissig"))

            async def arbeiten():
                while True:
                    await asyncio.sleep(ARBEITSTAKT)
                    veroeffentlicher.notiere_fortschritt("fleissig")

            arbeit = asyncio.create_task(arbeiten())
            await asyncio.wait_for(verbraucher._herzschlag("klemmt"), timeout=10)
            fleissig_lebt = not fleissig.done()
            fleissig.cancel()
            arbeit.cancel()
            return fleissig_lebt

        fleissig_lebt = self._lauf(korpus())
        self.assertTrue(
            fleissig_lebt,
            "Die gesunde Aufgabe wurde mit der klemmenden zusammen stillgelegt",
        )

    def test_fortschritt_wird_beim_veroeffentlichen_vermerkt(self):
        """Der Fortschrittsbeleg muss am Ereignisstrom haengen, nicht an einer
        eigenen Meldepflicht — sonst vergisst ihn der naechste neue Runner."""
        async def korpus():
            redis = FakeRedis()
            veroeffentlicher = LogPublisher(redis, "agent-test")
            veroeffentlicher.notiere_fortschritt("t1")
            await asyncio.sleep(GRENZE)
            await veroeffentlicher.publish("t1", "tool_call", {"name": "Bash"})
            return veroeffentlicher.stillstand_seit("t1")

        self.assertLess(asyncio.run(korpus()), GRENZE / 2)

    def test_aufgabe_wird_nach_ende_vergessen(self):
        """Sonst waechst die Zuordnung mit jeder Aufgabe endlos weiter."""
        veroeffentlicher = LogPublisher(FakeRedis(), "agent-test")
        veroeffentlicher.notiere_fortschritt("t1")
        veroeffentlicher.vergiss_aufgabe("t1")
        self.assertNotIn("t1", veroeffentlicher.last_activity_by_task)

    @staticmethod
    def _run_task_quelle() -> str:
        with open(tc.__file__, encoding="utf-8") as f:
            text = f.read()
        rest = text[text.index("    async def _run_task") + 4:]
        return rest[:rest.index("\n    async def ")]

    def test_uhr_startet_vor_dem_ersten_schlag(self):
        """Ohne Eintrag meldet ``stillstand_seit`` 0 Sekunden. Ein Herzschlag, der
        nie altern kann, waere wieder fail-open — die Uhr muss also ausdruecklich
        VOR dem Start der Coroutine gesetzt werden."""
        koerper = self._run_task_quelle()
        vermerk = koerper.index("notiere_fortschritt")
        start = koerper.index("_herzschlag(task_id)")
        self.assertLess(vermerk, start,
                        "Die Stillstandsuhr wird erst nach dem Herzschlag gesetzt")

    def test_aufraeumen_ist_zugesichert(self):
        """Auch wenn die Aufgabe mit einem Fehler endet — sonst waechst die
        Zuordnung mit jedem Lauf."""
        koerper = self._run_task_quelle()
        self.assertIn("vergiss_aufgabe", koerper.split("finally:")[-1])


class GrenzwertTests(unittest.TestCase):
    def test_grenze_ist_grosszuegig(self):
        """Ein einzelner Werkzeugaufruf darf lange dauern (ein Build, ein langer
        Testlauf). Zu knapp gesetzt wuerde diese Grenze #692 wiederholen."""
        self.assertGreaterEqual(tc.STILLSTAND_GRENZE_SEKUNDEN, 600)

    def test_grenze_faellt_nie_unter_eine_minute(self):
        with patch.dict("os.environ", {"STILLSTAND_GRENZE_SEKUNDEN": "1"}):
            self.assertGreaterEqual(tc._stillstand_grenze(), 60)

    def test_unsinn_in_der_umgebung_faellt_auf_den_standard(self):
        with patch.dict("os.environ", {"STILLSTAND_GRENZE_SEKUNDEN": "bald"}):
            self.assertEqual(tc._stillstand_grenze(), tc.STILLSTAND_GRENZE_SEKUNDEN)


if __name__ == "__main__":
    unittest.main()
