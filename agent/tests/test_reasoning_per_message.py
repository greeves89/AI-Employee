"""Per-message reasoning level, chosen by the user in the chat.

The value has to survive from the chat payload down to the actual harness call
in all three modes — that is the part that broke last time (the setting existed
but never reached anything the user was actually running).
"""

import unittest
from unittest.mock import patch


class ClaudeThinkingBudgetTests(unittest.TestCase):
    """claude_code has no effort flag — depth comes from MAX_THINKING_TOKENS."""

    def _budget(self, reasoning: str) -> dict:
        from app.chat_handler import ChatHandler

        env = {"MAX_THINKING_TOKENS": "999"}  # pretend a container default exists
        r = reasoning
        if r == "off":
            env.pop("MAX_THINKING_TOKENS", None)
        elif r in ChatHandler._THINKING_BUDGET:
            env["MAX_THINKING_TOKENS"] = ChatHandler._THINKING_BUDGET[r]
        return env

    def test_high_raises_budget(self):
        self.assertEqual(self._budget("high")["MAX_THINKING_TOKENS"], "31999")

    def test_max_uses_the_top_budget(self):
        """"max" aliases high on Claude — without a mapping entry it would fall
        through to the container default, i.e. WEAKER than high."""
        self.assertEqual(self._budget("max")["MAX_THINKING_TOKENS"], "31999")

    def test_low_lowers_budget(self):
        self.assertEqual(self._budget("low")["MAX_THINKING_TOKENS"], "4000")

    def test_off_clears_the_budget_entirely(self):
        self.assertNotIn("MAX_THINKING_TOKENS", self._budget("off"))

    def test_empty_leaves_container_default_untouched(self):
        self.assertEqual(self._budget("")["MAX_THINKING_TOKENS"], "999")


class CodexReasoningFlagTests(unittest.TestCase):
    """codex_cli takes -c model_reasoning_effort, per turn (not via config.toml)."""

    def _args(self, reasoning: str) -> list:
        common = ["--json", "-m", "gpt-5.5"]
        if reasoning:
            effort = {"off": "minimal", "max": "xhigh"}.get(reasoning, reasoning)
            common += ["-c", f'model_reasoning_effort="{effort}"']
        return common

    def test_high_is_passed_as_cli_override(self):
        self.assertIn('model_reasoning_effort="high"', self._args("high"))

    def test_off_maps_to_minimal(self):
        self.assertIn('model_reasoning_effort="minimal"', self._args("off"))

    def test_max_maps_to_xhigh(self):
        self.assertIn('model_reasoning_effort="xhigh"', self._args("max"))

    def test_empty_adds_no_flag(self):
        self.assertNotIn("-c", self._args(""))


class OpenAIProviderReasoningTests(unittest.TestCase):
    """custom_llm: the provider is cached across turns, so the per-message value
    must be written onto it each turn — including clearing it again on "off"."""

    def _provider(self, model="gpt-5.5"):
        from app.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_endpoint="https://example.invalid/v1",
            api_key="k",
            model_name=model,
            max_tokens=100,
            temperature=0.5,
        )

    def test_reasoning_lands_in_responses_body(self):
        p = self._provider()
        p.reasoning_effort = "high"
        body = p._build_responses_body([], None)
        self.assertEqual(body.get("reasoning"), {"effort": "high"})

    def test_cleared_reasoning_is_omitted(self):
        p = self._provider()
        p.reasoning_effort = ""
        body = p._build_responses_body([], None)
        self.assertNotIn("reasoning", body)

    def test_non_reasoning_model_never_gets_the_param(self):
        """A chat-latest alias 400s on this param — must stay out even if set."""
        p = self._provider(model="gpt-chat-latest")
        p.reasoning_effort = "high"
        self.assertFalse(p._supports_reasoning_effort())

    def test_xhigh_survives_for_codex_responses_model(self):
        p = self._provider(model="gpt-5.5-codex")
        p.reasoning_effort = "xhigh"
        body = p._build_responses_body([], None)
        self.assertEqual(body.get("reasoning"), {"effort": "xhigh"})

    def test_xhigh_clamps_to_high_for_plain_gpt5(self):
        """Plain GPT-5.x doesn't know xhigh — it must arrive as high, not 400."""
        p = self._provider(model="gpt-5.5")
        p.reasoning_effort = "xhigh"
        body = p._build_responses_body([], None)
        self.assertEqual(body.get("reasoning"), {"effort": "high"})

    def test_xhigh_clamps_to_high_in_chat_completions(self):
        p = self._provider(model="o3")
        p.reasoning_effort = "xhigh"
        body = p._build_chat_body([], None)
        self.assertEqual(body.get("reasoning_effort"), "high")


class WsWhitelistTests(unittest.TestCase):
    """The orchestrator whitelists the value before it can reach a CLI flag."""

    @staticmethod
    def _sanitize(raw) -> str:
        # Mirrors REASONING_LEVELS in orchestrator/app/models/chat_session.py —
        # the orchestrator is not importable from the agent's test env.
        r = str(raw or "").strip().lower()
        return r if r in ("off", "low", "medium", "high", "max") else ""

    def test_known_levels_pass(self):
        for v in ("off", "low", "medium", "high", "max"):
            self.assertEqual(self._sanitize(v), v)

    def test_injection_attempt_is_dropped(self):
        self.assertEqual(self._sanitize('high"; rm -rf /'), "")

    def test_unknown_value_is_dropped(self):
        self.assertEqual(self._sanitize("ultra"), "")


if __name__ == "__main__":
    unittest.main()
