"""Konfidenz-Routing muss in JEDER Laufzeit erreichbar sein (#389).

Harte Vorgabe des Nutzers: bei den Harnessen muss ALLES gleich sein — eine Luecke
heisst „nicht gebaut". Genau hier ist die Luecke besonders leicht zu uebersehen: ein
Werkzeug, das nur Claude Code kennt, sieht in der Oberflaeche vollstaendig aus, und
der Codex-Agent raet weiter. Auf dem Pi sind sieben von acht Agenten Codex.

Geprueft wird der Quelltext, nicht das laufende System — die Laufzeiten brauchen
Docker, Redis und einen Modellanbieter. Was ein Textvergleich hier zuverlaessig
faengt, ist der Fall, der wirklich vorkommt: jemand baut das Werkzeug an einer
Stelle ein und vergisst die anderen beiden.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MCP = ROOT / "agent/mcp/notification-server.mjs"
DEFINITIONS = ROOT / "agent/app/tools/definitions.py"
API_CLIENT = ROOT / "agent/app/tools/api_client.py"
EXECUTOR = ROOT / "agent/app/tools/executor.py"
AGENT_MANAGER = ROOT / "orchestrator/app/core/agent_manager.py"
APPROVALS = ROOT / "orchestrator/app/api/approvals.py"

TOOL = "escalate_if_unsure"


class ConfidenceParityTests(unittest.TestCase):
    def test_claude_code_has_the_tool(self):
        """Ueber den MCP-Server — der Weg, den Claude Code nutzt."""
        src = MCP.read_text()
        self.assertIn(f'name: "{TOOL}"', src, "Werkzeug fehlt in der MCP-Werkzeugliste")
        self.assertIn(f'case "{TOOL}"', src, "Werkzeug ist gelistet, aber nicht ausfuehrbar")

    def test_codex_and_custom_llm_have_the_tool(self):
        """Beide nutzen dieselbe Definitionsliste und denselben API-Client."""
        self.assertIn(f'"name": "{TOOL}"', DEFINITIONS.read_text(),
                      "Werkzeug fehlt in den Werkzeugdefinitionen (Codex + Custom-LLM)")
        self.assertIn(f"async def {TOOL}(", API_CLIENT.read_text(),
                      "Definition ohne Ausfuehrung — der Aufruf liefe ins Leere")

    def test_tool_is_never_blocked_by_the_whitelist(self):
        """Ein Agent, der die Rueckfrage nicht stellen KANN, raet stattdessen."""
        src = EXECUTOR.read_text()
        block = src[src.index("ALWAYS_ALLOWED_TOOLS"):src.index("def _get_allowed_categories")]
        self.assertIn(f'"{TOOL}"', block)


class ConfidenceEndpointTests(unittest.TestCase):
    def test_all_runtimes_use_the_same_endpoint(self):
        """Drei Laufzeiten, EIN Entscheidungspunkt.

        Wuerde eine davon selbst rechnen, gaebe es zwei Wahrheiten darueber, was
        „sicher genug" heisst — und die Schwelle des Betreibers waere fuer diese
        Laufzeit wirkungslos.
        """
        endpoint = "/approvals/confidence"
        self.assertIn(endpoint, MCP.read_text())
        self.assertIn(endpoint, API_CLIENT.read_text())
        self.assertIn('@router.post("/confidence")', APPROVALS.read_text())

    def test_no_runtime_decides_the_threshold_itself(self):
        """Kein Vergleich gegen eine fest eingebaute Zahl in den Laufzeiten."""
        for path in (MCP, API_CLIENT):
            src = path.read_text()
            block_start = src.find(TOOL)
            self.assertGreater(block_start, -1)
            block = src[block_start:block_start + 3000]
            for forbidden in ("threshold =", "threshold:", "< 70", ">= 70"):
                self.assertNotIn(
                    forbidden, block,
                    f"{path.name} entscheidet selbst ueber die Schwelle",
                )

    def test_system_prompt_tells_agents_the_tool_exists(self):
        """Ein Werkzeug, von dem der Agent nichts weiss, wird nie benutzt."""
        src = AGENT_MANAGER.read_text()
        self.assertIn(TOOL, src)
        # Und der Unterschied zur gewoehnlichen Freigabe muss dastehen, sonst wird
        # das eine fuer das andere benutzt.
        window = src[src.index(TOOL) - 200:src.index(TOOL) + 1200]
        self.assertIn("request_approval", window)


class ToolSchemaTests(unittest.TestCase):
    def test_both_schemas_require_the_same_fields(self):
        """Verlangte eine Laufzeit mehr als die andere, scheiterte derselbe Aufruf
        nur dort — und zwar erst zur Laufzeit."""
        mcp = MCP.read_text()
        start = mcp.index(f'name: "{TOOL}"')
        mcp_block = mcp[start:start + 2500]
        for field in ("confidence", "question", "context", "options", "task_id"):
            with self.subTest(field=field, runtime="claude_code"):
                self.assertIn(field, mcp_block)

        defs = DEFINITIONS.read_text()
        start = defs.index(f'"name": "{TOOL}"')
        def_block = defs[start:start + 2500]
        for field in ("confidence", "question", "context", "options", "task_id"):
            with self.subTest(field=field, runtime="codex"):
                self.assertIn(field, def_block)

        self.assertIn('required: ["confidence", "question"]', mcp_block)
        self.assertIn('"required": ["confidence", "question"]', def_block)


if __name__ == "__main__":
    unittest.main()
