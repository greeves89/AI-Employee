"""Ein abgebrochener Lauf arbeitet nicht heimlich weiter (#926).

Am 10.10.2026 galt ein Zeitplan-Lauf nach einer kurzen Redis-Störung („Timeout
reading from redis“) als gescheitert. Sein Claude-Prozess lief aber weiter — samt
Render- und Browser-Kindprozessen —, während der Neuversuch schon im selben
Arbeitsordner arbeitete. Beide überschrieben sich gegenseitig.
"""

import asyncio
import os
import signal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import agent_runner as ar
from app.log_publisher import LogPublisher
from app.proc_watchdog import prozessgruppe_beenden


def _lebt(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Zombie gilt nicht als lebend
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


async def _gruppe_mit_kind():
    """Prozess mit einem Kindprozess, der das Ende des Elternteils überleben würde."""
    proc = await asyncio.create_subprocess_exec(
        "sh", "-c", "sleep 60 & echo $!; wait", stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    kind = int((await proc.stdout.readline()).decode().strip())
    return proc, kind


class ProzessgruppeBeenden(unittest.IsolatedAsyncioTestCase):
    async def test_hauptprozess_und_kinder_sind_weg(self):
        proc, kind = await _gruppe_mit_kind()
        self.assertTrue(_lebt(kind))
        await prozessgruppe_beenden(proc, wartezeit=2)
        await asyncio.sleep(0.2)
        self.assertIsNotNone(proc.returncode)
        self.assertFalse(_lebt(kind), "der Kindprozess darf nicht verwaist weiterlaufen")

    async def test_schon_beendeter_prozess_ist_harmlos(self):
        proc = await asyncio.create_subprocess_exec("true", start_new_session=True)
        await proc.wait()
        await prozessgruppe_beenden(proc)
        await prozessgruppe_beenden(None)


class RunnerBeendetDenLaufVorDerFehlermeldung(unittest.IsolatedAsyncioTestCase):
    async def test_redis_stoerung_mitten_im_lauf(self):
        gestartet = {}
        echtes_starten = asyncio.create_subprocess_exec

        async def starten(*cmd, **kw):
            self.assertTrue(kw.get("start_new_session"), "eigene Prozessgruppe, sonst gehen Kinder nicht mit")
            kw.pop("stdin", None)
            proc = await echtes_starten("sh", "-c", "sleep 60 & echo $!; wait",
                                        stdin=asyncio.subprocess.PIPE, **kw)
            gestartet["proc"] = proc
            gestartet["kind"] = int((await proc.stdout.readline()).decode().strip())
            return proc

        async def stream(_proc):
            raise RuntimeError("Timeout reading from redis:6379")
            yield  # pragma: no cover — macht daraus einen Generator

        publisher = MagicMock(spec=LogPublisher)
        publisher.publish = AsyncMock()
        runner = ar.AgentRunner(publisher)
        import tempfile

        ordner = tempfile.mkdtemp()
        with patch.object(ar.settings, "workspace_dir", ordner), \
                patch.object(ar.asyncio, "create_subprocess_exec", starten), \
                patch.object(ar, "compose_prompt_bundle", lambda p, l: ""), \
                patch.object(runner, "_stream_output", stream), \
                patch("app.runner_hooks.zeitkontext", lambda: ""):
            ergebnis = await runner._execute_task_once("t1", "Auftrag")

        self.assertEqual(ergebnis["status"], "error", ergebnis)
        self.assertIn("proc", gestartet, ergebnis)
        await asyncio.sleep(0.2)
        self.assertIsNotNone(gestartet["proc"].returncode, "der CLI-Prozess muss beendet sein")
        self.assertFalse(_lebt(gestartet["kind"]), "auch seine Kindprozesse")


class ProtokollStoerungBrichtKeinenLaufAb(unittest.IsolatedAsyncioTestCase):
    async def test_publish_schluckt_redis_fehler(self):
        redis = MagicMock()
        redis.publish = AsyncMock(side_effect=TimeoutError("Timeout reading from redis:6379"))
        lp = LogPublisher.__new__(LogPublisher)
        lp.redis = redis
        lp.agent_id = "a1"
        lp._fortschritt = {}
        with patch.object(LogPublisher, "notiere_fortschritt", lambda self, t: None):
            await lp.publish("t1", "text", {"text": "x"})  # darf nicht werfen


if __name__ == "__main__":
    unittest.main()
