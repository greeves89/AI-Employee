"""Tests for account-provider to agent-harness mapping."""

import unittest

from app.api.agents import _mode_for_ai_account_provider, _model_provider_for_agent_mode
from app.core.agent_manager import AgentManager
from app.core.model_catalog import (
    coerce_model_for_mode,
    default_model_for_mode,
    is_model_allowed_for_mode,
    model_family,
)
from app.services.docker_service import _session_bind_path


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

    def test_codex_cli_session_mount_uses_codex_home(self):
        self.assertEqual(
            _session_bind_path({"AGENT_MODE": "codex_cli"}),
            "/home/agent/.codex",
        )
        self.assertEqual(
            _session_bind_path({"AGENT_MODE": "claude_code"}),
            "/home/agent/.claude",
        )


class ModelCatalogGuardTests(unittest.TestCase):
    """The provider/model guard: a harness may only run its own model family."""

    def test_model_family_classification(self):
        self.assertEqual(model_family("claude-sonnet-4-6"), "claude_code")
        # Bedrock ARN / Vertex-dated variants are still Claude.
        self.assertEqual(model_family("anthropic.claude-opus-4-8"), "claude_code")
        self.assertEqual(model_family("claude-haiku-4-5@20251001"), "claude_code")
        self.assertEqual(model_family("gpt-5.5"), "codex_cli")
        self.assertEqual(model_family("gpt-5-codex"), "codex_cli")
        self.assertEqual(model_family("o3-mini"), "codex_cli")
        # Unknown / custom territory.
        self.assertIsNone(model_family("gemini-2.5-pro"))
        self.assertIsNone(model_family(""))
        self.assertIsNone(model_family(None))

    def test_claude_code_accepts_only_claude(self):
        self.assertTrue(is_model_allowed_for_mode("claude_code", "claude-opus-4-8"))
        self.assertTrue(is_model_allowed_for_mode("claude_code", "anthropic.claude-sonnet-4-6"))
        self.assertFalse(is_model_allowed_for_mode("claude_code", "gpt-5.5"))
        self.assertFalse(is_model_allowed_for_mode("claude_code", "gemini-2.5-pro"))

    def test_codex_cli_accepts_only_gpt(self):
        self.assertTrue(is_model_allowed_for_mode("codex_cli", "gpt-5-codex"))
        self.assertTrue(is_model_allowed_for_mode("codex_cli", "o3-mini"))
        self.assertFalse(is_model_allowed_for_mode("codex_cli", "claude-sonnet-4-6"))

    def test_custom_llm_accepts_anything(self):
        for model in ["gpt-5.5", "claude-opus-4-8", "gemini-2.5-pro", "llama3.2", ""]:
            self.assertTrue(is_model_allowed_for_mode("custom_llm", model))

    def test_coerce_keeps_valid_and_replaces_invalid(self):
        # Valid stays untouched.
        self.assertEqual(coerce_model_for_mode("claude_code", "claude-opus-4-8"), "claude-opus-4-8")
        self.assertEqual(coerce_model_for_mode("codex_cli", "gpt-5-codex"), "gpt-5-codex")
        # Invalid / missing -> harness default (this is the "codex agent handed
        # the platform default claude-sonnet-4-6" regression that broke runs).
        self.assertEqual(coerce_model_for_mode("codex_cli", "claude-sonnet-4-6"),
                         default_model_for_mode("codex_cli"))
        self.assertEqual(coerce_model_for_mode("claude_code", "gpt-5.5"),
                         default_model_for_mode("claude_code"))
        self.assertEqual(coerce_model_for_mode("claude_code", None),
                         default_model_for_mode("claude_code"))
        # custom_llm passes through unchanged.
        self.assertEqual(coerce_model_for_mode("custom_llm", "llama3.2"), "llama3.2")

    def test_defaults_are_self_consistent(self):
        # Each harness default must itself pass the guard.
        for mode in ("claude_code", "codex_cli"):
            self.assertTrue(is_model_allowed_for_mode(mode, default_model_for_mode(mode)))


if __name__ == "__main__":
    unittest.main()
