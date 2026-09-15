"""Der Agent sendet ein Lebenszeichen, solange er arbeitet (#692) — geprueft
am Verhalten (#726).

Zwischen `task:started` und `task:completions` schrieb frueher NICHTS an der
Aufgabenzeile; der Waechter im Orchestrator mass damit die verstrichene Zeit
statt der Gesundheit des Arbeiters und brach nach 30 Minuten jede noch so
gesunde Aufgabe ab (31.08.2026: vier parallele Reviews, alle nach 30.3 min
„Worker still gestorben").

Die Zusicherungen standen bisher als Zeichenfenster im Quelltext — die
schlimmste davon `split("finally:")[:300]`: das ERSTE `finally:` der Datei,
300 Zeichen weit. Hier wird die Schleife mit einem Redis-Doppel laufen
gelassen und an dem abgelesen, was sie sendet; und `_run_task` wird gefahren,
um zu sehen, dass die Schleife am Ende wirklich beendet wird.

Die Orchestrator-Seite (Empfang, Waechter) liegt in
``orchestrator/tests/test_task_heartbeat_watchdog.py``.
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import app.task_consumer as tc


class _Redis:
    """Zaehlt Sendungen; kann auf Wunsch die ersten N scheitern lassen."""

    def __init__(self, fehler_vorne=0):
        self.gesendet: list[tuple[str, str]] = []
        self._fehler = fehler_vorne

    async def publish(self, kanal, nutzlast):
        if self._fehler:
            self._fehler -= 1
            raise ConnectionError("Redis kurz weg")
        self.gesendet.append((kanal, nutzlast))


async def _schlagen_lassen(consumer, task_id, schlaege):
    """Die Schleife laufen lassen, bis `schlaege` Sendeversuche stattfanden."""
    versuche = 0

    async def tick(_sek):
        nonlocal versuche
        versuche += 1
        if versuche > schlaege:
            raise asyncio.CancelledError   # so beendet sie auch die Produktion
        # KEIN asyncio.sleep(0) hier: `tc.asyncio` IST dieses asyncio, der
        # Aufruf liefe in diesen Ersatz zurueck.

    with patch.object(tc.asyncio, "sleep", tick):
        with unittest.TestCase().assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(consumer._herzschlag(task_id), timeout=5)


class DerAgentSendetEinLebenszeichenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.consumer = tc.TaskConsumer("agent-7")
        self.redis = _Redis()
        self.consumer.redis = self.redis

    async def test_es_gibt_eine_herzschlag_schleife(self):
        """Jeder Takt eine Sendung auf `task:heartbeat`, mit Aufgabe UND Agent
        — der Orchestrator ordnet danach zu."""
        await _schlagen_lassen(self.consumer, "t-1", schlaege=3)
        self.assertEqual([k for k, _ in self.redis.gesendet], ["task:heartbeat"] * 3)
        self.assertEqual(json.loads(self.redis.gesendet[0][1]),
                         {"task_id": "t-1", "agent_id": "agent-7"})

    async def test_ohne_kennung_schlaegt_nichts(self):
        await self.consumer._herzschlag(None)          # kehrt sofort zurueck
        self.assertEqual(self.redis.gesendet, [])

    async def test_der_takt_liegt_deutlich_unter_der_schwelle(self):
        """Ein einzelner verpasster Schlag darf keinen Abbruch ausloesen — die
        Waechter-Schwelle liegt bei mindestens 30 Minuten."""
        self.assertLessEqual(tc.TaskConsumer.HERZSCHLAG_SEKUNDEN, 120)
        gewartet = []

        async def tick(sek):
            gewartet.append(sek)
            raise asyncio.CancelledError

        with patch.object(tc.asyncio, "sleep", tick), self.assertRaises(asyncio.CancelledError):
            await self.consumer._herzschlag("t-1")
        self.assertEqual(gewartet, [tc.TaskConsumer.HERZSCHLAG_SEKUNDEN])

    async def test_ein_fehlschlag_reisst_die_aufgabe_nicht_mit(self):
        """Schlaegt das Senden fehl, wird beim naechsten Schlag erneut
        versucht — die Schleife stirbt nicht."""
        self.redis = _Redis(fehler_vorne=2)
        self.consumer.redis = self.redis
        await _schlagen_lassen(self.consumer, "t-1", schlaege=4)
        self.assertEqual(len(self.redis.gesendet), 2)   # die letzten zwei kamen an

    async def test_ein_abbruch_beendet_sie_aber(self):
        """CancelledError muss DURCH — sonst laesst sich die Schleife am Ende
        der Aufgabe nicht mehr einfangen."""
        async def tick(_sek):
            raise asyncio.CancelledError

        with patch.object(tc.asyncio, "sleep", tick), self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.consumer._herzschlag("t-1"), timeout=5)


class _Budget:
    def slot_for_task(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class DieSchleifeLebtGenauSoLangeWieDieAufgabeTests(unittest.IsolatedAsyncioTestCase):
    """Sequenziell gesendet waere sie waehrend der Arbeit still — genau der
    Zustand, den sie beheben soll. Und am Ende muss sie beendet werden, sonst
    bliebe je Aufgabe eine Schleife fuer immer stehen."""

    def _consumer(self, ergebnis):
        consumer = tc.TaskConsumer("agent-7")
        consumer._sem = asyncio.Semaphore(1)
        consumer.redis = SimpleNamespace(publish=AsyncMock())
        consumer._log_publisher = MagicMock(publish=AsyncMock(), publish_status=AsyncMock())
        gestartet = []

        class _Runner:
            async def execute_task(self, **kw):
                gestartet.append(("arbeit", list(losgelassen)))
                return await ergebnis()

        consumer._make_runner = lambda: _Runner()
        losgelassen: list = []
        abgebrochen: list = []

        def create_task(coro, *a, **kw):
            losgelassen.append(coro.__qualname__)
            coro.close()
            t = MagicMock()
            t.cancel = lambda: abgebrochen.append(coro.__qualname__)
            return t

        return consumer, gestartet, losgelassen, abgebrochen, create_task

    async def _fahren(self, ergebnis):
        consumer, gestartet, losgelassen, abgebrochen, create_task = self._consumer(ergebnis)
        await consumer._sem.acquire()
        with patch.object(tc, "get_run_budget", return_value=_Budget()), \
             patch.object(tc.asyncio, "create_task", side_effect=create_task):
            await asyncio.wait_for(consumer._run_task({"id": "t-1", "prompt": "mach was"}), timeout=5)
        return gestartet, losgelassen, abgebrochen

    async def test_sie_laeuft_neben_der_aufgabe(self):
        async def gut():
            return {"status": "completed"}

        gestartet, losgelassen, _ = await self._fahren(gut)
        self.assertTrue(any(n.endswith("_herzschlag") for n in losgelassen), losgelassen)
        # ... und zwar BEVOR die Arbeit beginnt, nicht erst danach.
        self.assertTrue(any(n.endswith("_herzschlag") for n in gestartet[0][1]), gestartet)

    async def test_sie_wird_am_ende_beendet(self):
        async def gut():
            return {"status": "completed"}

        _, _, abgebrochen = await self._fahren(gut)
        self.assertTrue(any(n.endswith("_herzschlag") for n in abgebrochen), abgebrochen)

    async def test_auch_wenn_die_aufgabe_scheitert(self):
        async def schlecht():
            raise RuntimeError("Runner tot")

        _, _, abgebrochen = await self._fahren(schlecht)
        self.assertTrue(any(n.endswith("_herzschlag") for n in abgebrochen), abgebrochen)


if __name__ == "__main__":
    unittest.main()
