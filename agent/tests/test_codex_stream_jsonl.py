"""_stream_jsonl() ist eine echte Ausfuehrungs-Regression, keine Text-Suche.

Vorfall 2026-08-06: JEDE Codex-Aufgabe (Chat wie proaktiv) schlug sofort mit
"NameError: name 'self' is not defined" fehl — noch bevor ein Werkzeug lief
oder das Modell etwas ausgegeben hatte. Ursache: _stream_jsonl ist eine
Modul-Funktion (keine Methode), enthielt aber `self.log_publisher...`,
kopiert aus dem benachbarten `collect_stderr` (das als Closure INNERHALB
einer Methode `self` legitim erreichen kann). Der bestehende
test_codex_stdout_ticks (test_chat_turn_idle_watchdog.py) prueft nur, ob der
TEXT "last_activity_at" im Quelltext vorkommt — das haette diesen Fehler nie
gefangen, weil "self.log_publisher.last_activity_at" den Text ebenso enthaelt
wie die korrigierte Zeile. Nur ein Test, der die Funktion wirklich AUSFUEHRT,
faengt einen NameError.
"""

import asyncio
import json
import unittest
from types import SimpleNamespace

from app.codex_runner import _stream_jsonl


class FakeStdout:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class StreamJsonlExecutionTests(unittest.TestCase):
    def test_runs_without_raising_and_yields_events(self):
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}})
        process = SimpleNamespace(stdout=FakeStdout([f"{line}\n".encode()]))
        log_publisher = SimpleNamespace(last_activity_at=0.0)

        async def run():
            return [ev async for ev in _stream_jsonl(process, log_publisher)]

        events = asyncio.run(run())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "item.completed")

    def test_updates_the_watchdog_heartbeat_on_the_passed_in_publisher(self):
        """Der eigentliche Zweck der Zeile — und der Beweis, dass log_publisher
        wirklich der uebergebene Parameter ist, nicht ein verirrtes `self`."""
        process = SimpleNamespace(stdout=FakeStdout([b'{"type":"x"}\n']))
        log_publisher = SimpleNamespace(last_activity_at=0.0)

        asyncio.run(self._drain(process, log_publisher))

        self.assertGreater(log_publisher.last_activity_at, 0.0)

    async def _drain(self, process, log_publisher):
        async for _ in _stream_jsonl(process, log_publisher):
            pass

    def test_empty_stdout_yields_nothing_and_does_not_raise(self):
        process = SimpleNamespace(stdout=FakeStdout([]))
        log_publisher = SimpleNamespace(last_activity_at=0.0)

        async def run():
            return [ev async for ev in _stream_jsonl(process, log_publisher)]

        self.assertEqual(asyncio.run(run()), [])


if __name__ == "__main__":
    unittest.main()
