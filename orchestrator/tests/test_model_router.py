"""Tests for content-based model routing (app/core/model_router.py)."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.core.model_router import classify_prompt, route_model
from app.core.task_router import TaskRouter


class ClassifyPromptTests(unittest.TestCase):
    def test_empty_prompt_is_simple(self):
        self.assertEqual(classify_prompt(""), "simple")

    def test_short_greeting_is_simple(self):
        self.assertEqual(classify_prompt("Hallo, danke dir!"), "simple")

    def test_debugging_request_is_complex(self):
        self.assertEqual(
            classify_prompt("Warum funktioniert dieser Code nicht? Analysiere den Fehler bitte im Detail."),
            "complex",
        )

    def test_code_fence_is_complex(self):
        self.assertEqual(classify_prompt("Hier ist mein Code:\n```python\ndef foo(): pass\n```"), "complex")

    def test_medium_length_prose_is_standard(self):
        self.assertEqual(
            classify_prompt("Kannst du mir eine kurze Zusammenfassung des heutigen Meetings schreiben?"),
            "standard",
        )


class RouteModelTests(unittest.TestCase):
    def test_uses_default_rules_when_none_given(self):
        self.assertEqual(route_model("Hallo"), "claude-haiku-4-5-20251001")

    def test_custom_rules_override_defaults(self):
        model = route_model("Hallo", rules={"simple": "gpt-5-mini"})
        self.assertEqual(model, "gpt-5-mini")

    def test_complex_tier_routes_to_configured_model(self):
        model = route_model(
            "Analysiere bitte diesen Stacktrace und erklaere die Exception im Detail.",
            rules={"complex": "claude-opus-5"},
        )
        self.assertEqual(model, "claude-opus-5")


def _router_with_agent(agent):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    db.execute = AsyncMock(return_value=result)
    return TaskRouter(db=db, redis=MagicMock(), load_balancer=MagicMock(), docker_service=None)


def _agent(config=None):
    return SimpleNamespace(config=config or {})


class RouteModelByContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_by_default_returns_none(self):
        router = _router_with_agent(_agent(config={}))
        result = await router._route_model_by_content("agent-1", "Analysiere diesen Fehler bitte.")
        self.assertIsNone(result)

    async def test_missing_agent_returns_none(self):
        router = _router_with_agent(None)
        result = await router._route_model_by_content("agent-1", "Analysiere diesen Fehler bitte.")
        self.assertIsNone(result)

    async def test_enabled_routes_by_content(self):
        router = _router_with_agent(_agent(config={
            "model_router": {"enabled": True, "rules": {"complex": "claude-opus-5"}},
        }))
        result = await router._route_model_by_content(
            "agent-1", "Analysiere diesen Stacktrace im Detail und erklaere die Exception."
        )
        self.assertEqual(result, "claude-opus-5")

    async def test_enabled_but_simple_prompt_routes_to_simple_tier(self):
        router = _router_with_agent(_agent(config={
            "model_router": {"enabled": True, "rules": {"simple": "gpt-5-mini"}},
        }))
        result = await router._route_model_by_content("agent-1", "Danke!")
        self.assertEqual(result, "gpt-5-mini")


if __name__ == "__main__":
    unittest.main()
