"""Chat (claude_code/codex_cli) had NO skill/marketplace context injection at all —
only a soft "use skill_search" hint the agent regularly ignored (#468). custom_llm
chat already builds this into its own system prompt on the first message, so it
must stay untouched to avoid a duplicated block.
"""

import unittest
from unittest.mock import MagicMock, patch


class PrepareTextSkillInjectionTests(unittest.TestCase):
    def _consumer(self):
        from app.chat_consumer import ChatConsumer

        return ChatConsumer(agent_id="agent-1")

    def _new_session_handler(self):
        handler = MagicMock()
        handler.session_id = None
        return handler

    def _resumed_session_handler(self):
        handler = MagicMock()
        handler.session_id = "sess-123"
        return handler

    @patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value="MARKETPLACE_BLOCK")
    @patch("app.runner_hooks.get_skills_context", return_value="SKILLS_BLOCK")
    @patch("app.runner_hooks.get_approval_rules_prefix", return_value="")
    @patch("app.chat_consumer.settings")
    def test_claude_code_new_session_gets_skill_context(
        self, mock_settings, _rules, mock_skills_ctx, mock_marketplace
    ):
        mock_settings.agent_mode = "claude_code"
        consumer = self._consumer()
        result = consumer._prepare_text(
            "Bau mir eine Webapp im Wizard-Style", None, "webapp", self._new_session_handler()
        )
        self.assertIn("SKILLS_BLOCK", result)
        self.assertIn("MARKETPLACE_BLOCK", result)
        mock_marketplace.assert_called_once_with("Bau mir eine Webapp im Wizard-Style"[:200])

    @patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value="MARKETPLACE_BLOCK")
    @patch("app.runner_hooks.get_skills_context", return_value="SKILLS_BLOCK")
    @patch("app.runner_hooks.get_approval_rules_prefix", return_value="")
    @patch("app.chat_consumer.settings")
    def test_codex_cli_new_session_gets_skill_context(
        self, mock_settings, _rules, mock_skills_ctx, mock_marketplace
    ):
        mock_settings.agent_mode = "codex_cli"
        consumer = self._consumer()
        result = consumer._prepare_text("hi", None, "webapp", self._new_session_handler())
        self.assertIn("SKILLS_BLOCK", result)
        self.assertIn("MARKETPLACE_BLOCK", result)

    @patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value="MARKETPLACE_BLOCK")
    @patch("app.runner_hooks.get_skills_context", return_value="SKILLS_BLOCK")
    @patch("app.runner_hooks.get_approval_rules_prefix", return_value="")
    @patch("app.chat_consumer.settings")
    def test_custom_llm_not_double_injected(
        self, mock_settings, _rules, mock_skills_ctx, mock_marketplace
    ):
        """LLMChatHandler already injects this into its own system prompt."""
        mock_settings.agent_mode = "custom_llm"
        consumer = self._consumer()
        result = consumer._prepare_text("hi", None, "webapp", self._new_session_handler())
        self.assertNotIn("SKILLS_BLOCK", result)
        self.assertNotIn("MARKETPLACE_BLOCK", result)
        mock_marketplace.assert_not_called()

    @patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value="MARKETPLACE_BLOCK")
    @patch("app.runner_hooks.get_skills_context", return_value="SKILLS_BLOCK")
    @patch("app.runner_hooks.get_approval_rules_prefix", return_value="")
    @patch("app.chat_consumer.settings")
    def test_resumed_session_not_reinjected(
        self, mock_settings, _rules, mock_skills_ctx, mock_marketplace
    ):
        """Only inject once per session (on the first turn), not on every follow-up."""
        mock_settings.agent_mode = "claude_code"
        consumer = self._consumer()
        result = consumer._prepare_text(
            "follow-up message", None, "webapp", self._resumed_session_handler()
        )
        self.assertNotIn("SKILLS_BLOCK", result)
        self.assertNotIn("MARKETPLACE_BLOCK", result)
        mock_marketplace.assert_not_called()

    @patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value="MARKETPLACE_BLOCK")
    @patch("app.runner_hooks.get_skills_context", return_value="SKILLS_BLOCK")
    @patch("app.runner_hooks.get_approval_rules_prefix", return_value="")
    @patch("app.chat_consumer.settings")
    def test_telegram_new_session_also_gets_skill_context(
        self, mock_settings, _rules, mock_skills_ctx, mock_marketplace
    ):
        mock_settings.agent_mode = "claude_code"
        consumer = self._consumer()
        result = consumer._prepare_text(
            "hi", {"chat_id": 123}, "telegram", self._new_session_handler()
        )
        self.assertIn("SKILLS_BLOCK", result)
        self.assertIn("MARKETPLACE_BLOCK", result)


if __name__ == "__main__":
    unittest.main()
