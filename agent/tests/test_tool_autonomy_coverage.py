"""Jedes Werkzeug muss entweder gekennzeichnet als immer-erlaubt oder einer
Autonomie-Kategorie zugeordnet sein — sonst faellt es lautlos durch (#197).

``ToolExecutor.execute()`` (executor.py:298) setzt die Autonomie-Matrix nur
durch, wenn ein Werkzeug in ``TOOL_CATEGORY_MAP`` steht; steht es dort NICHT
UND nicht in ``ALWAYS_ALLOWED_TOOLS``, ueberspringt der Code den Block
komplett — der Aufruf laeuft unkontrolliert durch, obwohl die Matrix/die
Befehlsregeln vorgeben, ihn zu gaten. Gefunden am 17.09.2026: 32 von 80
Werkzeugen betroffen, darunter ``computer_use`` (volle Kontrolle ueber den
ECHTEN Desktop des Nutzers), ``browser``, ``restart_own_container``,
``rebuild_app``/``start_app``/``stop_app``, ``create_skill``/``skill_update``.

Dieser Test haelt die Deckung fest, damit ein kuenftig neu hinzugefuegtes
Werkzeug den Build bricht statt die Luecke stillschweigend wieder aufzureissen.
"""
import re
import unittest
from pathlib import Path

from app.tools.executor import ALWAYS_ALLOWED_TOOLS, TOOL_CATEGORY_MAP

ROOT = Path(__file__).resolve().parents[2]
DEFINITIONS = ROOT / "agent/app/tools/definitions.py"

# Gueltige Kategorien — muessen den legacy_category-Werten in
# orchestrator/app/core/autonomy_matrix.py entsprechen, sonst greift die
# Matrix nie, egal was der Nutzer einstellt.
_GUELTIGE_KATEGORIEN = {
    "file_read", "file_write", "shell_exec", "system_config", "web_search",
    "custom", "external_communication", "purchase", "knowledge_write",
}


def _definition_tools() -> set[str]:
    return set(re.findall(r'"name":\s*"([a-z0-9_]+)"', DEFINITIONS.read_text()))


class ToolAutonomyCoverageTests(unittest.TestCase):
    def test_every_defined_tool_is_covered(self):
        alle = _definition_tools()
        abgedeckt = set(ALWAYS_ALLOWED_TOOLS) | set(TOOL_CATEGORY_MAP)
        ungedeckt = alle - abgedeckt
        self.assertEqual(
            ungedeckt, set(),
            "Diese Werkzeuge sind weder in ALWAYS_ALLOWED_TOOLS noch in "
            "TOOL_CATEGORY_MAP (executor.py) -- die Autonomie-Matrix/"
            "Befehlsregeln greifen fuer sie NICHT, egal was eingestellt ist. "
            f"Neu hinzufuegen und bewusst einordnen: {sorted(ungedeckt)}"
        )

    def test_no_tool_is_in_both_sets(self):
        ueberlappung = set(ALWAYS_ALLOWED_TOOLS) & set(TOOL_CATEGORY_MAP)
        self.assertEqual(ueberlappung, set(), f"Widerspruechlich eingeordnet: {sorted(ueberlappung)}")

    def test_every_category_is_a_real_autonomy_bucket(self):
        unbekannt = set(TOOL_CATEGORY_MAP.values()) - _GUELTIGE_KATEGORIEN
        self.assertEqual(
            unbekannt, set(),
            f"Diese Kategorien kennt die Autonomie-Matrix nicht, die Zeile "
            f"greift also nie: {sorted(unbekannt)}"
        )

    def test_high_risk_tools_are_gated_not_always_allowed(self):
        """Regressionswache: diese vier duerfen NIE in ALWAYS_ALLOWED_TOOLS
        landen, egal welche Refaktorierung als naechstes kommt."""
        hochrisiko = {"computer_use", "browser", "bash", "restart_own_container"}
        self.assertTrue(hochrisiko.issubset(TOOL_CATEGORY_MAP.keys()), hochrisiko - TOOL_CATEGORY_MAP.keys())
        self.assertFalse(hochrisiko & set(ALWAYS_ALLOWED_TOOLS))


if __name__ == "__main__":
    unittest.main()
