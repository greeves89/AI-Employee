"""Tests for account-provider to agent-harness mapping."""

import unittest

from app.api.agents import _mode_for_ai_account_provider, _model_provider_for_agent_mode
from app.core.agent_manager import AgentManager


class AgentHarnessMappingTests(unittest.TestCase):
    def test_anthropic_account_maps_to_claude_code(self):
        self.assertEqual(AgentManager._mode_for_ai_provider("anthropic"), "claude_code")
        self.assertEqual(_mode_for_ai_account_provider("anthropic"), "claude_code")
        self.assertEqual(_model_provider_for_agent_mode("claude_code", "anthropic"), "anthropic")

    def test_openai_account_maps_to_codex_cli(self):
        self.assertEqual(AgentManager._mode_for_ai_provider("openai"), "codex_cli")
        self.assertEqual(_mode_for_ai_account_provider("openai"), "codex_cli")
        self.assertEqual(_model_provider_for_agent_mode("codex_cli", "openai"), "codex")

    def test_local_and_google_accounts_stay_custom(self):
        for provider in ["google", "ollama", "lm-studio", "azure-openai"]:
            self.assertEqual(AgentManager._mode_for_ai_provider(provider), "custom_llm")
            self.assertEqual(_mode_for_ai_account_provider(provider), "custom_llm")

    def test_cli_account_env_exposes_only_matching_cli_credentials(self):
        anthropic_cfg = {
            "provider_type": "anthropic",
            "api_key": "secret-anthropic",
            "model_name": "claude-sonnet-4-6",
        }
        openai_cfg = {
            "provider_type": "openai",
            "api_key": "secret-openai",
            "model_name": "gpt-5.5",
        }

        self.assertEqual(
            AgentManager._cli_account_env("claude_code", anthropic_cfg),
            {
                "DEFAULT_MODEL": "claude-sonnet-4-6",
                "ANTHROPIC_API_KEY": "secret-anthropic",
            },
        )
        self.assertEqual(
            AgentManager._cli_account_env("codex_cli", openai_cfg),
            {
                "CODEX_HOME": "/home/agent/.codex",
                "DEFAULT_MODEL": "gpt-5.5",
                "OPENAI_API_KEY": "secret-openai",
            },
        )
        self.assertEqual(AgentManager._cli_account_env("codex_cli", anthropic_cfg), {})
        self.assertEqual(AgentManager._cli_account_env("claude_code", openai_cfg), {})


if __name__ == "__main__":
    unittest.main()
