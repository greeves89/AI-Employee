"""Delegierte Pfade müssen beim Empfänger existieren.

Am 2026-08-12 beauftragte der CEO-Agent Mr. Design und Dr. Code mit Arbeit an
``/workspace/projects/kunden-systemlandkarte``. Bei IHM gibt es das Verzeichnis —
sein Volume ist 854 MB groß und enthält drei Projekte. Bei den beiden anderen
nicht: deren Volumes sind 36 KB groß und waren es seit ihrer Anlage.

Jeder Agent hat sein eigenes ``/workspace``, und das ist **so gewollt**. Die
Anleitung sagte das aber nirgends — sie listete nur „Workspace: /workspace/
(persistent across tasks)". Der Lead verschickte seine lokalen Pfade also in
gutem Glauben, die Empfänger fanden nichts, meldeten „keine Artefakte
ermittelbar", und der Lead machte die Arbeit am Ende selbst.

Von außen sah das aus wie: nur der Chef arbeitet, die anderen verweigern.

Gemeinsamer Boden ist ausschließlich ``/shared/``.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_MANAGER = ROOT / "orchestrator/app/core/agent_manager.py"
DEFINITIONS = ROOT / "agent/app/tools/definitions.py"
MCP_SERVER = ROOT / "agent/mcp/orchestrator-server.mjs"


class TheInstructionsSayWorkspaceIsPrivateTests(unittest.TestCase):
    def setUp(self):
        self.text = AGENT_MANAGER.read_text()

    def test_privacy_is_stated(self):
        self.assertIn("YOURS ALONE", self.text,
                      "Ohne diesen Hinweis verschickt ein Lead seine eigenen "
                      "Pfade und wundert sich, dass niemand etwas findet")

    def test_shared_is_named_as_the_only_common_ground(self):
        self.assertIn("only directory every agent sees", self.text)

    def test_both_ways_out_are_offered(self):
        """Verbieten allein hilft nicht — der Lead braucht den Ersatzweg."""
        for hint in ("Put the files in `/shared/` first", "self-contained work"):
            with self.subTest(hint):
                self.assertIn(hint, self.text)

    def test_the_receiving_side_is_covered_too(self):
        self.assertIn("do not guess what they meant", self.text)


class TheToolItselfWarnsTests(unittest.TestCase):
    """Die Anleitung liest der Agent einmal beim Start; die Werkzeugbeschreibung
    steht in JEDEM Zug vor ihm. Der Hinweis gehört an beide Stellen — und in
    beide Laufzeiten, sonst hat eine davon ihn nicht."""

    @staticmethod
    def _werkzeug_python(name: str) -> str:
        """Die Werkzeugdefinition, wie das MODELL sie bekommt: die Liste wird
        als Literal ausgewertet (Python fuegt gestueckelte Zeichenketten dabei
        zusammen), das Werkzeug ueber seinen Namen gewaehlt und als JSON
        ausgegeben — die ganze Definition, nicht 2500 Zeichen ab dem Namen."""
        import ast
        import json

        baum = ast.parse(DEFINITIONS.read_text())
        for knoten in baum.body:
            ziel = getattr(knoten, "target", None) or (knoten.targets[0] if isinstance(knoten, ast.Assign) else None)
            if isinstance(ziel, ast.Name) and ziel.id in ("LOCAL_TOOLS", "ORCHESTRATOR_TOOLS"):
                for werkzeug in ast.literal_eval(knoten.value):
                    if werkzeug["function"]["name"] == name:
                        return json.dumps(werkzeug)
        raise AssertionError(f"Werkzeug {name} nicht in definitions.py")

    @staticmethod
    def _werkzeug_mcp(name: str) -> str:
        """Das Objekt-Literal `{ name: "<name>", ... }` bis zur schliessenden
        Klammer — Klammertiefe zaehlen statt Zeichen. Umbrueche und die
        `" + "`-Stueckelung werden wie bei der Python-Seite aufgehoben."""
        import re

        text = MCP_SERVER.read_text()
        anker = text.index(f'name: "{name}"')
        auf = text.rindex("{", 0, anker)
        tiefe, i = 0, auf
        for i in range(auf, len(text)):
            if text[i] in "{[(":
                tiefe += 1
            elif text[i] in "}])":
                tiefe -= 1
                if tiefe == 0:
                    break
        block = text[auf:i + 1]
        joined = re.sub(r'"\s*\+?\s*\n\s*"', "", block)   # Stueckelung aufheben
        return re.sub(r"\s+", " ", joined)

    def test_custom_llm_definition_warns(self):
        block = self._werkzeug_python("delegate_and_wait")
        self.assertIn("cannot see yours", block)
        self.assertIn("/shared/", block)

    def test_the_mcp_server_warns_identically(self):
        block = self._werkzeug_mcp("delegate_and_wait")
        self.assertIn("cannot see yours", block)
        self.assertIn("/shared/", block)


if __name__ == "__main__":
    unittest.main()
