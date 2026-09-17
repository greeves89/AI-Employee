"""Issue #197 Teil 2: die Entscheidungslogik hinter dem Claude-Code-PreToolUse-
Hook. Reine Funktion, kein aiohttp/Docker noetig — siehe health.py fuer die
HTTP-Anbindung.
"""
import unittest
from unittest.mock import patch

from app.tools.pretooluse_hook import decide


def _entscheidung(tool_name: str) -> str:
    return decide(tool_name)["hookSpecificOutput"]["permissionDecision"]


class NativeAlwaysAllowedTests(unittest.TestCase):
    def test_read_only_and_meta_tools_are_allowed(self):
        for name in ("Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite",
                     "BashOutput", "KillShell", "Task", "SlashCommand"):
            with self.subTest(tool=name):
                self.assertEqual(_entscheidung(name), "allow")


class NativeGatedToolsTests(unittest.TestCase):
    def test_bash_write_edit_are_allowed_when_the_category_is_whitelisted(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories",
                    return_value={"shell_exec", "file_write"}):
            for name in ("Bash", "Write", "Edit", "NotebookEdit"):
                with self.subTest(tool=name):
                    self.assertEqual(_entscheidung(name), "allow")

    def test_bash_is_denied_when_shell_exec_is_not_whitelisted(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={"file_write"}):
            result = decide("Bash")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("shell_exec", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_write_is_denied_when_file_write_is_not_whitelisted(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={"shell_exec"}):
            self.assertEqual(_entscheidung("Write"), "deny")

    def test_no_restrictions_configured_means_everything_is_allowed(self):
        # _get_allowed_categories() returns None when no approval rules exist
        # (or when the orchestrator is unreachable) — same "no rules = no
        # restriction" contract the custom_llm executor already has.
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=None):
            for name in ("Bash", "Write", "Edit", "NotebookEdit"):
                with self.subTest(tool=name):
                    self.assertEqual(_entscheidung(name), "allow")


class McpToolTests(unittest.TestCase):
    def test_an_always_allowed_custom_llm_tool_resolves_through_the_mcp_prefix(self):
        # mcp__orchestrator__create_task -> bare name "create_task" -> already
        # in executor.py's ALWAYS_ALLOWED_TOOLS.
        self.assertEqual(_entscheidung("mcp__orchestrator__create_task"), "allow")

    def test_a_categorized_custom_llm_tool_is_gated_through_the_mcp_prefix(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=set()):
            result = decide("mcp__brain__brain_contribute")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_categorized_custom_llm_tool_is_allowed_when_whitelisted(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={"knowledge_write"}):
            self.assertEqual(_entscheidung("mcp__brain__brain_contribute"), "allow")

    def test_computer_use_via_mcp_is_gated_not_always_allowed(self):
        # The highest-risk tool in the whole catalog (controls the user's
        # REAL desktop) — must never resolve to an unconditional allow.
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=set()):
            self.assertEqual(_entscheidung("mcp__desktop__computer_use"), "deny")


class UnknownToolTests(unittest.TestCase):
    def test_a_completely_unknown_tool_name_is_denied_not_silently_allowed(self):
        """The exact bug class #197 closed for the custom_llm runtime: an
        unclassified tool must never default to allow."""
        result = decide("SomeFutureBuiltinTool")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Unbekanntes Werkzeug", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_an_mcp_tool_whose_bare_name_is_unclassified_is_denied(self):
        result = decide("mcp__somefutureserver__totally_new_tool")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_malformed_mcp_prefix_is_denied(self):
        result = decide("mcp__onlyoneseparator")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")


class ResponseShapeTests(unittest.TestCase):
    def test_allow_response_matches_the_documented_hook_schema(self):
        result = decide("Read")
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_deny_response_includes_a_reason(self):
        result = decide("NotAKnownTool")
        out = result["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertTrue(out["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
