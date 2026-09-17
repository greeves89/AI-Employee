"""Issue #197 Teil 2: die Entscheidungslogik hinter dem Claude-Code-PreToolUse-
Hook. Reine Funktion, kein aiohttp/Docker noetig — siehe health.py fuer die
HTTP-Anbindung.
"""
import unittest
from unittest.mock import patch

from app.tools.pretooluse_hook import decide


def _entscheidung(tool_name: str) -> str:
    return decide(tool_name)["hookSpecificOutput"]["permissionDecision"]


# Eine konfigurierte, enge Freigabeliste: alles, was hier "allow" bekommt,
# ist unabhaengig von der Autonomiestufe erlaubt.
_ENG = patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={"knowledge_write"})


class NativeAlwaysAllowedTests(unittest.TestCase):
    def test_read_only_and_meta_tools_are_allowed(self):
        for name in ("Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite",
                     "BashOutput", "KillShell", "Task", "SlashCommand"):
            with self.subTest(tool=name):
                self.assertEqual(_entscheidung(name), "allow")

    def test_the_current_names_of_the_same_tools_are_allowed_even_under_a_whitelist(self):
        """Regression 2026-09-17: die erste Fassung kannte nur die alten Namen
        `Task`/`SlashCommand`; `Agent` (Subagent), `Skill` und `ToolSearch`
        (Schemata zurueckgestellter Werkzeuge nachladen) wurden auf einer
        laufenden Anlage abgelehnt — damit gab es keinen Gegenleser, keine
        Skill-Anweisung und kein M365-/Mail-Werkzeug mehr."""
        with _ENG:
            for name in ("Agent", "Skill", "ToolSearch", "ListAgents", "TaskOutput",
                         "TaskStop", "SendMessage", "Workflow", "ScheduleWakeup",
                         "CronCreate", "CronList", "CronDelete", "ReportFindings",
                         "ListMcpResourcesTool", "ReadMcpResourceTool",
                         "ReadMcpResourceDirTool"):
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

    def test_monitor_is_shell_exec_and_worktrees_are_file_write(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={"file_write"}):
            self.assertEqual(_entscheidung("Monitor"), "deny")
            self.assertEqual(_entscheidung("EnterWorktree"), "allow")
            self.assertEqual(_entscheidung("ExitWorktree"), "allow")
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={"shell_exec"}):
            self.assertEqual(_entscheidung("Monitor"), "allow")
            self.assertEqual(_entscheidung("EnterWorktree"), "deny")

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

    def test_the_desktop_servers_real_tool_names_share_computer_uses_category(self):
        """Der Desktop-Server bietet `computer_screenshot`, `computer_click`, ...
        an — nicht `computer_use`. Sie sind dieselbe Faehigkeit und bekommen
        dieselbe Kategorie: gesperrt ohne, erlaubt mit Freigabe."""
        from app.tools.executor import TOOL_CATEGORY_MAP
        kategorie = TOOL_CATEGORY_MAP["computer_use"]
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=set()):
            for name in ("mcp__desktop__computer_screenshot", "mcp__desktop__computer_click"):
                with self.subTest(tool=name):
                    self.assertEqual(_entscheidung(name), "deny")
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value={kategorie}):
            self.assertEqual(_entscheidung("mcp__desktop__computer_screenshot"), "allow")

    def test_own_container_logs_are_readable_under_any_whitelist(self):
        with _ENG:
            self.assertEqual(_entscheidung("mcp__read-logs__read_logs"), "allow")


class UnknownToolTests(unittest.TestCase):
    """Unbekannte Werkzeuge unter einer KONFIGURIERTEN Freigabeliste."""

    def test_a_completely_unknown_tool_name_is_denied_not_silently_allowed(self):
        """The exact bug class #197 closed for the custom_llm runtime: an
        unclassified tool must never default to allow."""
        with _ENG:
            result = decide("SomeFutureBuiltinTool")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Unbekanntes Werkzeug", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_an_mcp_tool_whose_bare_name_is_unclassified_is_denied(self):
        with _ENG:
            result = decide("mcp__somefutureserver__totally_new_tool")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_malformed_mcp_prefix_is_denied(self):
        with _ENG:
            result = decide("mcp__onlyoneseparator")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")


class NoWhitelistTests(unittest.TestCase):
    """Keine Regeln oder die L4-Regel "Alles erlaubt" — beides liefert None.
    Dann gibt es nichts, wogegen ein unbekanntes Werkzeug verstossen koennte:
    derselbe Vertrag wie fuer Bash/Write. Die erste Fassung sperrte hier
    trotzdem — und nahm einem L4-Agenten die M365-, Mail- und Desktop-Server."""

    def test_unknown_native_and_mcp_tools_are_allowed_without_a_whitelist(self):
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=None):
            for name in ("SomeFutureBuiltinTool", "mcp__msgraph__ms_search",
                         "mcp__email__email_send", "mcp__somefutureserver__totally_new_tool"):
                with self.subTest(tool=name):
                    self.assertEqual(_entscheidung(name), "allow")

    def test_an_empty_whitelist_is_a_whitelist_and_still_denies_unknown_tools(self):
        # set() heisst "Regeln vorhanden, keine Kategorie freigegeben" — das
        # ist das Gegenteil von None und bleibt streng.
        with patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=set()):
            self.assertEqual(_entscheidung("mcp__msgraph__ms_search"), "deny")


class ResponseShapeTests(unittest.TestCase):
    def test_allow_response_matches_the_documented_hook_schema(self):
        result = decide("Read")
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_deny_response_includes_a_reason(self):
        with _ENG:
            result = decide("NotAKnownTool")
        out = result["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertTrue(out["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
