"""Ein gescheiterter Codex-Lauf darf ein bereits erzeugtes Teilergebnis nicht
wegwerfen, und der Exit-Code muss sich aus der Fehlermeldung filtern lassen
(Issue #710, Punkte 1+2).

Vorher wurde ``result_data`` im Fehlerpfad komplett durch ein neues Dict
ersetzt (``{"status": "error", "error": error}``) — jeder bis dahin erzeugte
Text (``result``/``text``) ging damit verloren, selbst wenn die CLI vor dem
Abbruch bereits etwas ausgegeben hatte.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.codex_runner import CodexAgentRunner


def _jsonl_event(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


class _FakeStream:
    """Minimal stand-in for a StreamReader: async read()/readline() over
    a fixed list of chunks, then EOF."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    async def readline(self) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _FakeStdin:
    def write(self, _data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(self, stdout_chunks: list[bytes], stderr_chunks: list[bytes], returncode: int):
        self.stdout = _FakeStream(stdout_chunks)
        self.stderr = _FakeStream(stderr_chunks)
        self.stdin = _FakeStdin()
        self._returncode = returncode

    async def wait(self) -> int:
        return self._returncode

    def send_signal(self, _sig) -> None:
        pass


class ATeilergebnisVorDemAbbruchBleibtErhaltenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.publisher = MagicMock()
        self.publisher.publish = AsyncMock()
        self.publisher.publish_chat = AsyncMock()
        self.publisher.last_activity_at = 0.0
        self.runner = CodexAgentRunner(self.publisher)

        self.auth_patch = patch("app.codex_runner._codex_auth_problem", return_value=None)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

        # _codex_env() creates real directories under CODEX_HOME (MCP config
        # etc.) — point it at a throwaway tmpdir instead of the real
        # container path (/home/agent), which isn't writable in this sandbox.
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env_patch = patch.dict(os.environ, {"CODEX_HOME": self.tmpdir.name})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.rotate_patch = patch(
            "app.codex_runner.codex_auth_sync.push_if_rotated", AsyncMock(return_value=False)
        )
        self.rotate_patch.start()
        self.addCleanup(self.rotate_patch.stop)

    async def test_text_vor_dem_absturz_bleibt_im_ergebnis(self):
        """Die CLI gibt einen Textblock aus, dann scheitert sie ohne
        Abschluss-Ereignis (kein 'completed') und mit leerem stderr — genau
        der reale Fall vom 06.09.2026."""
        events = [
            _jsonl_event({"type": "response.output_text.delta", "text": "teilweise fertig"}),
        ]
        process = _FakeProcess(stdout_chunks=events, stderr_chunks=[], returncode=1)

        with patch(
            "app.codex_runner.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
        ):
            result = await self.runner._run_codex("t1", "prompt", "model", stream="task")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result.get("result"), "teilweise fertig")
        self.assertEqual(result.get("text"), "teilweise fertig")

    async def test_der_exit_code_steht_als_stabiles_praefix_in_der_meldung(self):
        process = _FakeProcess(stdout_chunks=[], stderr_chunks=[], returncode=17)

        with patch(
            "app.codex_runner.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
        ):
            result = await self.runner._run_codex("t1", "prompt", "model", stream="task")

        self.assertTrue(result["error"].startswith("[codex_exit=17]"))

    async def test_leeres_stderr_wird_ausdruecklich_benannt_statt_verschwiegen(self):
        """Genau das war der gemeldete Fehler: ein generisches 'exited with
        code 1' ohne jeden Hinweis darauf, dass stderr leer war."""
        process = _FakeProcess(stdout_chunks=[], stderr_chunks=[], returncode=1)

        with patch(
            "app.codex_runner.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
        ):
            result = await self.runner._run_codex("t1", "prompt", "model", stream="task")

        self.assertIn("keine Ausgabe auf stderr", result["error"])

    async def test_stderr_text_wird_weiterhin_in_die_meldung_uebernommen(self):
        process = _FakeProcess(
            stdout_chunks=[], stderr_chunks=[b"invalid_grant: token expired\n"], returncode=1,
        )

        with patch(
            "app.codex_runner.asyncio.create_subprocess_exec", AsyncMock(return_value=process)
        ):
            result = await self.runner._run_codex("t1", "prompt", "model", stream="task")

        self.assertIn("invalid_grant: token expired", result["error"])


if __name__ == "__main__":
    unittest.main()
