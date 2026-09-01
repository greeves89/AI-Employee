"""Sprach-Werkzeug `save_memory` schrieb bisher per Rohinsert direkt in
`agent_memories` (category hart auf "fact", kein category/importance-Parameter
im Tool-Schema) — am save_memory_core-Pfad vorbei, den MCP-`memory_save` nutzt.
Damit fehlten Dedup/Supersede/Contradiction-Handling komplett, und der
Systemprompt-Text ("category: preference, importance: 5") war Behauptung ohne
Wirkung, weil das Tool diese Parameter gar nicht annehmen konnte.

Diese Tests fixieren die Korrektur: `_save_memory` baut jetzt ein `MemorySave`
und läuft über `save_memory_core` (in-process, kein HTTP-Umweg) — gleiches
Dedup/Contradiction-Verhalten wie beim MCP-Tool, category/importance werden
tatsächlich durchgereicht statt verworfen.
"""

import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

# Local test runs don't have the docker SDK (only the container image does) —
# stub it so importing the API layer works (same pattern as test_reflection.py).
_docker_stub = types.ModuleType("docker")
_docker_stub.from_env = lambda: None
_docker_errors_stub = types.ModuleType("docker.errors")
_docker_errors_stub.NotFound = type("NotFound", (Exception,), {})
_docker_errors_stub.APIError = type("APIError", (Exception,), {})
_docker_stub.errors = _docker_errors_stub
_docker_models_stub = types.ModuleType("docker.models")
_docker_containers_stub = types.ModuleType("docker.models.containers")
_docker_containers_stub.Container = type("Container", (), {})
_docker_models_stub.containers = _docker_containers_stub
_docker_stub.models = _docker_models_stub
sys.modules.setdefault("docker", _docker_stub)
sys.modules.setdefault("docker.errors", _docker_errors_stub)
sys.modules.setdefault("docker.models", _docker_models_stub)
sys.modules.setdefault("docker.models.containers", _docker_containers_stub)

from app.api.memory import MemoryConflict  # noqa: E402
from app.models.memory import AgentMemory  # noqa: E402
from app.services import realtime_voice_session as rvs  # noqa: E402


class _FakeDB:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


def _voice(agent_id="a1"):
    s = rvs.RealtimeVoiceSession.__new__(rvs.RealtimeVoiceSession)
    s.agent_id = agent_id
    return s


def _existing():
    m = AgentMemory(agent_id="a1", category="fact", key="k", content="alt")
    m.id = 7
    return m


class SaveMemoryVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._patchers = [
            patch("app.db.session.async_session_factory", lambda: _FakeDB()),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_empty_content_asks_without_touching_db(self):
        with patch("app.api.memory.save_memory_core", new=AsyncMock()) as core:
            out = await _voice()._save_memory("")
        core.assert_not_awaited()
        self.assertIn("Was soll ich mir merken", out)

    async def test_default_category_and_importance(self):
        with patch("app.api.memory.save_memory_core",
                    new=AsyncMock(return_value=(_existing(), None))) as core:
            await _voice(agent_id="a1")._save_memory("Kunde mag GmbH-Struktur")
        body = core.await_args.args[1]
        self.assertEqual(body.agent_id, "a1")
        self.assertEqual(body.category, "fact")
        self.assertEqual(body.importance, 3)
        self.assertEqual(body.source, "conversation")

    async def test_explicit_category_and_importance_pass_through(self):
        with patch("app.api.memory.save_memory_core",
                    new=AsyncMock(return_value=(_existing(), None))) as core:
            await _voice()._save_memory(
                "Nutzer heißt ab jetzt Luna", key="anrede",
                category="preference", importance=5,
            )
        body = core.await_args.args[1]
        self.assertEqual(body.category, "preference")
        self.assertEqual(body.importance, 5)
        self.assertEqual(body.key, "anrede")

    async def test_invalid_category_falls_back_to_fact(self):
        with patch("app.api.memory.save_memory_core",
                    new=AsyncMock(return_value=(_existing(), None))) as core:
            await _voice()._save_memory("x", category="quatsch")
        self.assertEqual(core.await_args.args[1].category, "fact")

    async def test_out_of_range_importance_falls_back_to_default(self):
        with patch("app.api.memory.save_memory_core",
                    new=AsyncMock(return_value=(_existing(), None))) as core:
            await _voice()._save_memory("x", importance=99)
        self.assertEqual(core.await_args.args[1].importance, 3)

    async def test_contradiction_retries_once_with_override(self):
        # `body` is the SAME mutable object across both calls (the source
        # flips .override in place before retrying) — record its override
        # flag AT CALL TIME, not from the stored call args afterwards.
        seen_override = []

        async def _side_effect(db, body, allow_supersede=True):
            seen_override.append(body.override)
            if len(seen_override) == 1:
                raise MemoryConflict("contradiction", _existing(), 0.9)
            return _existing(), None

        core = AsyncMock(side_effect=_side_effect)
        with patch("app.api.memory.save_memory_core", new=core):
            out = await _voice()._save_memory("Server ist jetzt 10.0.0.9")
        self.assertEqual(core.await_count, 2)
        self.assertEqual(seen_override, [False, True])
        self.assertIn("Gemerkt", out)

    async def test_write_failure_is_caught_not_raised(self):
        with patch("app.api.memory.save_memory_core",
                    new=AsyncMock(side_effect=RuntimeError("db down"))):
            out = await _voice()._save_memory("x")
        self.assertIn("nicht geklappt", out)


if __name__ == "__main__":
    unittest.main()
