"""Der Abbruch-Zuhoerer im Agenten — geprueft am Verhalten (#726).

Der Kanal ``agent:{id}:task:cancel`` wurde vom Orchestrator seit jeher
besendet, aber niemand hoerte zu (Nutzerbericht 21.08.2026: dreimal
„abbrechen", dreimal „ist gestoppt", die Aufgabe lief weiter). Die Zusicherung
„jetzt hoert jemand zu und stoppt GENAU die genannte Aufgabe" stand bisher als
Zeichenfenster hinter ``async def _cancel_listener`` — ein laengerer Kommentar
haette den Test gebrochen, ein auskommentiertes ``runner.interrupt()`` ihn
bestehen lassen. Hier wird der Zuhoerer mit einem Redis-Doppel gefuettert und
an den Runnern abgelesen, was er getan hat.

Zusammen mit ``orchestrator/tests/test_task_cancel_really_stops.py`` (Router-
und Sprachseite) deckt das alle vier Schichten des Befundes ab.
"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import app.task_consumer as tc


class _Runner:
    def __init__(self, laeuft=True):
        self.is_running = laeuft
        self.unterbrochen = 0

    async def interrupt(self):
        self.unterbrochen += 1


class _PubSub:
    """Liefert die gestellten Nachrichten der Reihe nach, danach Stille."""

    def __init__(self, nachrichten, danach):
        self._nachrichten = list(nachrichten)
        self._danach = danach          # wird nach der letzten Nachricht gerufen
        self.abonniert: list[str] = []
        self.abbestellt: list[str] = []
        self.geschlossen = False

    async def subscribe(self, kanal):
        self.abonniert.append(kanal)

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._nachrichten:
            eintrag = self._nachrichten.pop(0)
            # Eine Zeichenkette ist eine Abbruch-Nachricht; ein Dict wird roh
            # durchgereicht — so kann das Doppel auch eine Abonnement-
            # Bestaetigung liefern, die der Zuhoerer NICHT als Kennung lesen darf.
            return eintrag if isinstance(eintrag, dict) else {"type": "message", "data": eintrag}
        self._danach()
        return None

    async def unsubscribe(self, kanal):
        self.abbestellt.append(kanal)

    async def aclose(self):
        self.geschlossen = True


class _Verbindung:
    def __init__(self, pubsub):
        self._pubsub = pubsub
        self.geschlossen = False

    def pubsub(self):
        return self._pubsub

    async def aclose(self):
        self.geschlossen = True


def _zuhoerer(nachrichten, runner):
    consumer = tc.TaskConsumer("agent-7")
    consumer._runner_by_task = dict(runner)

    def aufhoeren():
        consumer.running = False

    pubsub = _PubSub(nachrichten, aufhoeren)
    return consumer, pubsub, _Verbindung(pubsub)


_echtes_sleep = asyncio.sleep


async def _kurz_abgeben(_sekunden):
    """Ersatz fuer `asyncio.sleep`, der an die Schleife ABGIBT statt nur
    sofort zurueckzukehren: ein `AsyncMock()` gibt nie ab, und eine Mutation
    `while self.running` -> `while True` haette dann eine Endlosschleife ohne
    Yield erzeugt, an der `wait_for` nichts ausrichten kann — der Test HAENGT
    statt rot zu werden."""
    await _echtes_sleep(0)


async def _laufen_lassen(consumer, verbindung):
    with patch.object(tc.aioredis, "from_url", return_value=verbindung), \
         patch.object(tc.asyncio, "sleep", _kurz_abgeben):
        await asyncio.wait_for(consumer._cancel_listener(), timeout=5)


class TheAgentListensOnTheChannelTheRouterSendsOnTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_subscribes_to_exactly_that_channel(self):
        consumer, pubsub, verbindung = _zuhoerer([], {})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual(pubsub.abonniert, ["agent:agent-7:task:cancel"])

    async def test_it_can_stop_one_task_not_only_everything(self):
        eine, andere = _Runner(), _Runner()
        consumer, _, verbindung = _zuhoerer(["t-eine"], {"t-eine": eine, "t-andere": andere})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual(eine.unterbrochen, 1)
        self.assertEqual(andere.unterbrochen, 0)

    async def test_a_subscribe_confirmation_is_not_read_as_a_task_id(self):
        """redis liefert nach dem Abonnieren `{"type": "subscribe", "data": 1}`.
        Ohne den Typ-Filter wuerde `1` als Kennung gelesen — oder, bei
        `data == "all"`-Vergleich auf einem int, der Zuhoerer stuerbe."""
        alle = _Runner()
        consumer, _, verbindung = _zuhoerer(
            [{"type": "subscribe", "data": 1}, {"type": "subscribe", "data": "all"}, "t-alle"],
            {"t-alle": alle, "t-andere": _Runner()})
        await _laufen_lassen(consumer, verbindung)
        # Nur die echte Nachricht wirkt: genau EIN Runner, genau EINMAL.
        self.assertEqual(alle.unterbrochen, 1)
        self.assertEqual(consumer._runner_by_task["t-andere"].unterbrochen, 0)

    async def test_all_stops_everything_that_runs(self):
        eine, andere = _Runner(), _Runner()
        consumer, _, verbindung = _zuhoerer(["all"], {"t-eine": eine, "t-andere": andere})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual((eine.unterbrochen, andere.unterbrochen), (1, 1))

    async def test_a_runner_that_already_finished_is_left_alone(self):
        fertig = _Runner(laeuft=False)
        consumer, _, verbindung = _zuhoerer(["t-fertig"], {"t-fertig": fertig})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual(fertig.unterbrochen, 0)

    async def test_an_unknown_id_does_not_kill_the_listener(self):
        """Ein Abbruch fuer eine Aufgabe, die hier nicht (mehr) laeuft, ist
        Alltag — er darf den Zuhoerer nicht mitreissen: die NAECHSTE Nachricht
        muss noch ankommen."""
        eine = _Runner()
        consumer, _, verbindung = _zuhoerer(["unbekannt", "t-eine"], {"t-eine": eine})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual(eine.unterbrochen, 1)

    async def test_a_failing_interrupt_does_not_kill_the_listener_either(self):
        class _Stur(_Runner):
            async def interrupt(self):
                raise RuntimeError("will nicht")

        stur, brav = _Stur(), _Runner()
        consumer, _, verbindung = _zuhoerer(["t-stur", "t-brav"], {"t-stur": stur, "t-brav": brav})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual(brav.unterbrochen, 1)

    async def test_it_leaves_no_open_connection_behind(self):
        consumer, pubsub, verbindung = _zuhoerer([], {})
        await _laufen_lassen(consumer, verbindung)
        self.assertEqual(pubsub.abbestellt, ["agent:agent-7:task:cancel"])
        self.assertTrue(pubsub.geschlossen)
        self.assertTrue(verbindung.geschlossen)


class TheListenerRunsAlongsideTheQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_lets_it_loose_as_its_own_task(self):
        """Liefe er in derselben Schleife, kaeme er erst dran, wenn gerade
        keine Aufgabe verarbeitet wird — also genau dann nicht, wenn man ihn
        braucht."""
        consumer = tc.TaskConsumer("agent-7")
        losgelassen = []

        def create_task(coro, *a, **kw):
            losgelassen.append(getattr(coro, "__qualname__", repr(coro)))
            coro.close()
            t = MagicMock()
            t.add_done_callback = lambda cb: None
            return t

        async def brpop(*a, **kw):
            consumer.running = False      # eine Runde reicht
            return None

        redis = SimpleNamespace(brpop=brpop)
        with patch.object(tc.aioredis, "from_url", return_value=redis), \
             patch.object(tc, "LogPublisher",
                          return_value=MagicMock(publish=AsyncMock(), publish_status=AsyncMock())), \
             patch.object(tc, "read_pids_limits", return_value=(None, None)), \
             patch.object(tc, "_max_parallel_tasks", return_value=1), \
             patch.object(tc.asyncio, "create_task", side_effect=create_task):
            await asyncio.wait_for(consumer.start(), timeout=5)
        self.assertTrue(any(n.endswith("_cancel_listener") for n in losgelassen), losgelassen)


class TheMappingIsCleanedUpAfterwardsTests(unittest.IsolatedAsyncioTestCase):
    """Sonst waechst `_runner_by_task` mit jeder Aufgabe und zeigt auf tote
    Runner — und ein spaeterer Abbruch traefe ein Objekt, das laengst fertig
    ist."""

    async def test_the_runner_is_registered_during_and_gone_after_the_task(self):
        consumer = tc.TaskConsumer("agent-7")
        consumer._sem = asyncio.Semaphore(1)
        await consumer._sem.acquire()
        consumer.redis = SimpleNamespace(publish=AsyncMock())
        consumer._log_publisher = MagicMock(publish=AsyncMock(), publish_status=AsyncMock())
        waehrend = {}

        class _Runner:
            async def execute_task(self, **kw):
                waehrend.update(consumer._runner_by_task)
                return {"status": "completed"}

        runner = _Runner()
        consumer._make_runner = lambda: runner

        class _Budget:
            def slot_for_task(self):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        with patch.object(tc, "get_run_budget", return_value=_Budget()), \
             patch.object(tc.asyncio, "create_task",
                          side_effect=lambda coro, *a, **kw: (coro.close(), MagicMock())[1]):
            await asyncio.wait_for(
                consumer._run_task({"id": "t-eine", "prompt": "mach was"}), timeout=5)

        self.assertIs(waehrend.get("t-eine"), runner, "waehrend der Arbeit muss die Zuordnung stehen")
        self.assertNotIn("t-eine", consumer._runner_by_task, "danach muss sie weg sein")
        # Und die Aufgabe ist regulaer zu Ende gemeldet worden.
        kanaele = [c.args[0] for c in consumer.redis.publish.await_args_list]
        self.assertIn("task:completions", kanaele)
        ende = json.loads(consumer.redis.publish.await_args_list[-1].args[1])
        self.assertEqual(ende["task_id"], "t-eine")


if __name__ == "__main__":
    unittest.main()
