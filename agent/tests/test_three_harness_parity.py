"""Alle drei Laufzeiten, ein Werkzeugsatz — die vollständige Matrix.

Die Vorgabe ist alt und eindeutig: was Claude Code kann, müssen Codex UND
Custom-LLM auch können. Trotzdem sind heute zwei Lücken aufgefallen, und beide
haben denselben Grund — **jede Laufzeit bezieht ihre Werkzeuge anders**:

* **Claude Code** — stdio-MCP-Server, eingetragen in ``agent/app/main.py``
* **Codex** — stdio-MCP-Server, eingetragen in ``agent/app/codex_runner.py``
  (eigene, kürzere Liste!)
* **Custom LLM** — ``agent/app/tools/definitions.py`` plus MCP über **HTTP**;
  stdio-Server erreicht diese Laufzeit **nie**

Drei Listen, die niemand gegeneinander geprüft hat. Das Ergebnis stand beim
Kunden im Chat: ein Custom-LLM-Agent ohne ``delegate_and_wait`` **beschrieb** die
Delegation, statt sie auszuführen — samt erfundener Statustabelle, während alle
Agenten im Leerlauf waren.

Dieser Test hält die Matrix fest. Er prüft **Fähigkeiten**, nicht Anzeigen — der
ältere ``test_agent_toolset`` vergleicht den Katalog fürs ``/``-Menü und war
grün, während sechs Team-Werkzeuge fehlten.

Jede Abweichung braucht einen **begründeten** Eintrag. Eine Lücke ohne Grund ist
keine Entscheidung, sondern ein Versehen — und genau die kosten Kundenvertrauen.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / "agent/mcp"

#: Server, die Claude Code startet — aber Codex bewusst NICHT bekommt.
CODEX_DELIBERATE_GAPS: dict[str, str] = {
    "bash-approval": (
        "Claude-Code-Eigenheit: haengt am PreToolUse-Hook der Claude-CLI. Codex "
        "hat diesen Haken nicht; seine Freigaben laufen ueber die Executor-Whitelist."
    ),
}

#: Server, deren Werkzeuge Custom-LLM ueber HTTP-MCP erreicht statt ueber
#: definitions.py. Sie muessen dort NICHT nachgebaut sein.
CUSTOM_LLM_VIA_HTTP: dict[str, str] = {
    "msgraph": "Kommt zur Laufzeit ueber den MCP-HTTP-Client (mcp_-Praefix, vorab aktiviert).",
    "email": "Wie msgraph — HTTP-MCP, kein stdio.",
    "hyperframes": "HTTP-MCP (Video-Dienst).",
    "brain": "HTTP-MCP (Vault als Bearer-Server).",
    "bash-approval": "Kein Werkzeugsatz, nur ein Freigabe-Haken.",
    "computer-use": "Rechnersteuerung — offen, siehe test_computer_use_parity.",
    "read-logs": "Offen, siehe unten.",
    "skill": "Teilweise in definitions.py; Rest offen.",
    "memory": "In definitions.py nachgebaut (memory_*).",
    "notification": "In definitions.py nachgebaut (notify_user).",
}


def _servers(path: Path) -> set[str]:
    """Welche stdio-MCP-Server diese Laufzeit startet."""
    return set(re.findall(r"mcp/([a-z-]+)-server\.mjs", path.read_text()))


def _tools_of(server: str) -> set[str]:
    p = MCP_DIR / f"{server}-server.mjs"
    if not p.exists():
        return set()
    return set(re.findall(r'^\s{4,8}name:\s*"([a-z0-9_]+)"', p.read_text(), re.M))


CLAUDE = _servers(ROOT / "agent/app/main.py")
CODEX = _servers(ROOT / "agent/app/codex_runner.py")
DEFINITIONS = set(re.findall(
    r'"name":\s*"([a-z0-9_]+)"',
    (ROOT / "agent/app/tools/definitions.py").read_text(),
))


class CodexGetsWhatClaudeGetsTests(unittest.TestCase):
    """Codex und Claude Code laufen beide auf stdio — es gibt keinen technischen
    Grund fuer zwei verschiedene Listen."""

    def test_no_server_is_missing_for_codex(self):
        missing = CLAUDE - CODEX - set(CODEX_DELIBERATE_GAPS)
        self.assertEqual(
            missing, set(),
            "Diese MCP-Server bekommt Claude Code und Codex nicht — ein "
            "Codex-Agent kann die Funktionen dahinter schlicht nicht: "
            + ", ".join(sorted(missing))
            + ". Nachtragen in codex_runner._ensure_codex_mcp_config oder mit "
              "Begruendung in CODEX_DELIBERATE_GAPS.",
        )

    def test_gaps_carry_a_reason(self):
        for server, reason in CODEX_DELIBERATE_GAPS.items():
            with self.subTest(server):
                self.assertGreater(len(reason.strip()), 30,
                                   f"{server}: Begruendung fehlt oder ist zu duenn")

    def test_codex_does_not_get_servers_claude_lacks(self):
        """Andersherum genauso — sonst waere Claude die Laufzeit mit der Luecke."""
        extra = CODEX - CLAUDE
        self.assertEqual(extra, set(), f"Nur Codex hat: {sorted(extra)}")


class CustomLlmGetsTheTeamToolsTests(unittest.TestCase):
    """Der Orchestrator-MCP ist der Team-Baukasten. Er ist stdio — Custom-LLM
    erreicht ihn NIE ueber MCP und braucht ihn deshalb in definitions.py."""

    def test_every_orchestrator_tool_is_rebuilt(self):
        missing = _tools_of("orchestrator") - DEFINITIONS
        self.assertEqual(
            missing, set(),
            "Custom-LLM fehlen diese Team-Werkzeuge — ein Agent ohne sie "
            "BESCHREIBT die Handlung, statt sie auszufuehren: "
            + ", ".join(sorted(missing)),
        )

    def test_the_four_central_ones_are_in_the_core_set(self):
        """Im Katalog zu stehen genuegt nicht: was der Agent erst ueber
        ``search_tools`` finden muss, findet er in der Praxis nicht."""
        from app.llm_chat_handler import _core_tool_names

        core = _core_tool_names()
        for tool in ("delegate_and_wait", "list_my_team",
                     "list_team_tasks", "get_tasks_status"):
            with self.subTest(tool):
                self.assertIn(tool, core)


class TheMatrixIsDocumentedTests(unittest.TestCase):
    """Jede verbleibende Abweichung ist benannt — keine stillen Luecken."""

    def test_every_claude_server_is_accounted_for_in_custom_llm(self):
        unexplained = CLAUDE - set(CUSTOM_LLM_VIA_HTTP) - {"orchestrator"}
        self.assertEqual(
            unexplained, set(),
            "Fuer diese Server ist nicht festgehalten, wie Custom-LLM an ihre "
            "Werkzeuge kommt: " + ", ".join(sorted(unexplained)),
        )

    def test_the_lists_are_not_empty(self):
        """Ein leerer Parser waere ein gruener Test ohne Aussage — genau die
        Sorte, die heute dreimal einen Ausfall ueberlebt hat."""
        self.assertGreater(len(CLAUDE), 5)
        self.assertGreater(len(CODEX), 5)
        self.assertGreater(len(DEFINITIONS), 50)


if __name__ == "__main__":
    unittest.main()
