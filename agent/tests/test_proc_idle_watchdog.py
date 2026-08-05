"""Der Stillstands-Wachhund fuer Unterprozesse — die eine Stelle fuer alle Pfade.

Feste Gesamtdauern haben arbeitende Agenten abgeschossen: im Chat nach 600s, bei
Agent-zu-Agent-Nachrichten nach 300s. Hier wird geprueft, dass ein Prozess, der
etwas von sich gibt, weiterlaufen darf — und einer, der verstummt, faellt.
"""

import asyncio
import unittest

from app.proc_watchdog import ProcessIdleTimeout, communicate_with_idle_timeout


async def _spawn(script: str):
    return await asyncio.create_subprocess_exec(
        "python3", "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


class IdleWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_but_chatty_process_survives(self):
        """Laenger als die Grenze, aber es kommt laufend Ausgabe — muss durchlaufen.
        Genau der Fall, der vorher abgeschossen wurde."""
        proc = await _spawn(
            "import sys,time\n"
            "for i in range(8):\n"
            "    print(i, flush=True); time.sleep(0.25)\n"
        )
        out, _err = await communicate_with_idle_timeout(proc, idle_limit=1.0)
        self.assertIn(b"7", out)

    async def test_silent_process_is_aborted(self):
        proc = await _spawn("import time; time.sleep(30)")
        with self.assertRaises(ProcessIdleTimeout):
            await communicate_with_idle_timeout(proc, idle_limit=1.0)
        self.assertIsNotNone(proc.returncode or await proc.wait())

    async def test_hard_cap_catches_endless_chatter(self):
        """Ein Prozess, der ewig Ausgabe erzeugt, darf nicht ewig laufen."""
        proc = await _spawn(
            "import time\n"
            "while True:\n"
            "    print('x', flush=True); time.sleep(0.05)\n"
        )
        with self.assertRaises(ProcessIdleTimeout):
            await communicate_with_idle_timeout(proc, idle_limit=10.0, hard_cap=1.0)

    async def test_stderr_also_counts_as_life(self):
        proc = await _spawn(
            "import sys,time\n"
            "for i in range(6):\n"
            "    print(i, file=sys.stderr, flush=True); time.sleep(0.25)\n"
        )
        _out, err = await communicate_with_idle_timeout(proc, idle_limit=1.0)
        self.assertIn(b"5", err)

    async def test_activity_callback_fires(self):
        """Darueber meldet der Aufrufer das Lebenszeichen nach aussen weiter."""
        hits = []
        proc = await _spawn("print('hallo', flush=True)")
        await communicate_with_idle_timeout(
            proc, idle_limit=5.0, on_activity=lambda: hits.append(1)
        )
        self.assertTrue(hits)

    async def test_stdin_is_delivered(self):
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", "import sys; print(sys.stdin.read().strip().upper())",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await communicate_with_idle_timeout(
            proc, idle_limit=5.0, stdin_input=b"hallo"
        )
        self.assertIn(b"HALLO", out)


if __name__ == "__main__":
    unittest.main()
