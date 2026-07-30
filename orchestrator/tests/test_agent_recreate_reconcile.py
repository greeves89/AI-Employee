"""Guard test for issue #364: the agent recreate path must be idempotent against
an already-existing container *name* and must not spam WARNING when the lifecycle
sweep runs with an unconnected Redis client.

Two coupled failures were observed together in ``platform-errors.log``:

  1. ``AgentManager.restart_agent`` removed the old container by the stored
     ``container_id`` only, then created a new one under the deterministic name
     ``ai-agent-<slug>-<id>``. A stale ``container_id`` (pointing at an already
     removed container) while a container under the fixed *name* still existed
     made ``docker create`` collide with a 409 (name already in use).

  2. ``_publish_event`` / ``_cancel_open_chats`` called ``self.redis.client.publish``
     with ``self.redis.client is None`` (a freshly instantiated ``RedisService`` in
     the recreate path), which the try/except caught but logged as WARNING twice per
     sweep tick → recurring log spam that masks real lifecycle errors.

This is a source-level guard (same style as ``test_resilient_session_wiring``): it
asserts the fix is wired in and catches any future regression that reintroduces
the id-only removal or the unguarded publish.
"""

import ast
import unittest
from pathlib import Path

_AGENT_MANAGER = (
    Path(__file__).resolve().parent.parent / "app" / "core" / "agent_manager.py"
)


def _func_source(tree: ast.Module, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function {name!r} not found in agent_manager.py")


class TestAgentRecreateReconcile(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = _AGENT_MANAGER.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_restart_agent_removes_by_name_and_id(self) -> None:
        """restart_agent must stop/remove by BOTH container_id and the fixed name."""
        src = _func_source(self.tree, self.source, "restart_agent")
        self.assertIn(
            "for ref in [agent.container_id, container_name]",
            src,
            "restart_agent must reconcile by both container_id AND container_name "
            "before creating a new container (else a stale id + existing name -> 409).",
        )

    def test_restart_agent_defines_name_before_removal(self) -> None:
        """container_name must be defined before the removal loop uses it."""
        src = _func_source(self.tree, self.source, "restart_agent")
        name_def = src.index('container_name = f"ai-agent-')
        removal = src.index("for ref in [agent.container_id, container_name]")
        self.assertLess(
            name_def,
            removal,
            "container_name must be assigned before the reconciliation loop references it.",
        )

    def test_publish_event_guards_none_client(self) -> None:
        src = _func_source(self.tree, self.source, "_publish_event")
        self.assertIn(
            "if self.redis.client is None:",
            src,
            "_publish_event must early-return (debug, not warn) when there is no "
            "connected Redis client, so the recreate sweep does not spam WARNING.",
        )

    def test_cancel_open_chats_guards_none_client(self) -> None:
        src = _func_source(self.tree, self.source, "_cancel_open_chats")
        self.assertIn(
            "if self.redis.client is None:",
            src,
            "_cancel_open_chats must early-return when there is no connected Redis client.",
        )


if __name__ == "__main__":
    unittest.main()
