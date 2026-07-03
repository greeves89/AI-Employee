"""Tests for task-dispatch model coercion (issue #282 follow-up).

A delegated/inherited model must be coerced to the target agent's harness mode at
task-dispatch time, or a codex_cli agent handed a claude-* model fails at runtime
("not supported when using Codex with a ChatGPT account").
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.core.model_catalog import default_model_for_mode
from app.core.task_router import TaskRouter


def _router_with_agent(agent):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    db.execute = AsyncMock(return_value=result)
    return TaskRouter(db=db, redis=MagicMock(), load_balancer=MagicMock(), docker_service=None)


def _agent(mode="claude_code", config=None):
    return SimpleNamespace(mode=mode, config=config or {})


class TaskModelCoercionTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_model_to_codex_agent_is_coerced(self):
        router = _router_with_agent(_agent(mode="codex_cli"))
        out = await router._coerce_task_model_for_agent("a1", "claude-opus-4-8")
        self.assertEqual(out, default_model_for_mode("codex_cli"))

    async def test_claude_code_agent_with_codex_provider_is_coerced(self):
        router = _router_with_agent(
            _agent(mode="claude_code", config={"model_provider": "codex"})
        )
        out = await router._coerce_task_model_for_agent("a1", "claude-opus-4-8")
        self.assertEqual(out, default_model_for_mode("codex_cli"))

    async def test_compatible_model_passes_through(self):
        router = _router_with_agent(_agent(mode="codex_cli"))
        out = await router._coerce_task_model_for_agent("a1", "gpt-5-codex")
        self.assertEqual(out, "gpt-5-codex")

    async def test_claude_model_to_claude_agent_passes_through(self):
        router = _router_with_agent(_agent(mode="claude_code"))
        out = await router._coerce_task_model_for_agent("a1", "claude-opus-4-8")
        self.assertEqual(out, "claude-opus-4-8")

    async def test_none_model_stays_none(self):
        router = _router_with_agent(_agent(mode="codex_cli"))
        out = await router._coerce_task_model_for_agent("a1", None)
        self.assertIsNone(out)

    async def test_missing_agent_passes_model_through(self):
        router = _router_with_agent(None)
        out = await router._coerce_task_model_for_agent("ghost", "claude-opus-4-8")
        self.assertEqual(out, "claude-opus-4-8")


if __name__ == "__main__":
    unittest.main()
