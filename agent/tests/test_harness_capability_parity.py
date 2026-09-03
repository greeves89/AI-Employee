"""Was Claude Code kann, muss Custom-LLM auch können.

Es gab schon einen „Paritätstest" — der vergleicht aber den **Katalog für die
`/`-Anzeige** im Chat, nicht die **Fähigkeiten zwischen den Laufzeiten**. Er war
grün, während einem Custom-LLM-Agenten das Werkzeug fehlte, um einem anderen
Agenten etwas aufzutragen.

Die Folge stand beim Kunden im Chat: der CEO-Agent meldete „Alle drei
beauftragten Sub-Agents sind aktuell aktiv" mit einer Statustabelle — während
die Übersicht alle Agenten als Idle, 0 % CPU, ohne Warteschlange zeigte. Er hatte
kein Werkzeug zum Delegieren und tat, was ein Modell dann tut: er BESCHRIEB die
Handlung. Zwölf Sekunden, zwei Züge — in der Zeit wird nichts beauftragt.

Der Unterschied zwischen den Laufzeiten ist real und begründet: Claude Code
startet stdio-MCP-Server (``agent/mcp/*.mjs``), der Custom-LLM-Lauf holt seine
MCP-Werkzeuge über HTTP und erreicht stdio-Server nie. Alles aus einem
stdio-Server muss deshalb in ``definitions.py`` nachgebaut sein — oder hier
ausdrücklich als bewusste Auslassung stehen.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH_MCP = ROOT / "agent/mcp/orchestrator-server.mjs"
DEFINITIONS = ROOT / "agent/app/tools/definitions.py"

#: Werkzeuge des Orchestrator-MCP, die Custom-LLM BEWUSST nicht hat — mit Grund.
#: Ein leerer Eintrag ist keine Erlaubnis: wer hier etwas einträgt, muss sagen,
#: warum die Laufzeit ohne auskommt.
DELIBERATE_GAPS: dict[str, str] = {}


def _mcp_tools() -> set[str]:
    return set(re.findall(r'^\s{4,8}name:\s*"([a-z0-9_]+)"', ORCH_MCP.read_text(), re.M))


def _definition_tools() -> set[str]:
    return set(re.findall(r'"name":\s*"([a-z0-9_]+)"', DEFINITIONS.read_text()))


class OrchestratorToolParityTests(unittest.TestCase):
    """Der Orchestrator-MCP ist der Team-Baukasten — er MUSS überall ankommen."""

    def test_every_orchestrator_tool_exists_for_custom_llm(self):
        missing = _mcp_tools() - _definition_tools() - set(DELIBERATE_GAPS)
        self.assertEqual(
            missing, set(),
            "Diese Werkzeuge hat Claude Code und Custom-LLM nicht. Ein Agent ohne "
            "das passende Werkzeug BESCHREIBT die Handlung, statt sie auszufuehren "
            "— genau so entstand die erfundene Statustabelle beim Kunden. "
            "Nachbauen in definitions.py + api_client.py, oder mit Begruendung in "
            "DELIBERATE_GAPS eintragen: " + ", ".join(sorted(missing)),
        )

    def test_gaps_are_justified(self):
        for tool, reason in DELIBERATE_GAPS.items():
            with self.subTest(tool):
                self.assertTrue(reason.strip(),
                                f"{tool} steht als bewusste Luecke ohne Begruendung")


class DelegationReachesTheModelTests(unittest.TestCase):
    """Im Katalog zu stehen genügt nicht — es muss auch ankommen."""

    def test_delegate_and_wait_is_defined(self):
        self.assertIn("delegate_and_wait", _definition_tools())

    def test_it_is_in_the_core_set_not_only_the_catalogue(self):
        """Was der Agent erst ueber ``search_tools`` finden muss, findet er in der
        Praxis nicht — und redet dann darueber, statt es zu tun."""
        from app.llm_chat_handler import _core_tool_names

        self.assertIn("delegate_and_wait", _core_tool_names())

    def test_the_executor_allows_it(self):
        from app.tools.executor import ToolExecutor  # noqa: F401
        from pathlib import Path as _P

        src = (ROOT / "agent/app/tools/executor.py").read_text()
        self.assertIn('"delegate_and_wait"', src)

    def test_the_api_client_implements_it(self):
        """Der Executor sucht die Methode per ``getattr`` — fehlt sie, antwortet er
        mit „not implemented", und der Agent redet wieder."""
        from app.tools.api_client import OrchestratorAPIClient

        self.assertTrue(hasattr(OrchestratorAPIClient, "delegate_and_wait"))

    def test_the_description_forbids_announcing_without_calling(self):
        """Der eigentliche Fehler war nicht die fehlende Faehigkeit, sondern die
        Ansage ohne Handlung. Das steht in der Beschreibung."""
        src = DEFINITIONS.read_text()
        block = src.split('"name": "delegate_and_wait"')[1][:1200]
        self.assertIn("without calling", block.lower())


class UnfinishedWorkIsNotSuccessTests(unittest.TestCase):
    """Ein Auftrag, der noch laeuft, darf nicht als erledigt gemeldet werden."""

    def test_pending_tasks_are_named_as_pending(self):
        from pathlib import Path as _P

        src = (ROOT / "agent/app/tools/api_client.py").read_text()
        block = src.split("async def delegate_and_wait")[1].split("\n    async def ")[0]
        self.assertIn("laeuft noch", block,
                      "Nicht fertige Auftraege muessen als solche benannt werden — "
                      "sie stillschweigend wegzulassen ist genau der Fehler, der "
                      "die erfundene Statustabelle erzeugt hat")


if __name__ == "__main__":
    unittest.main()
