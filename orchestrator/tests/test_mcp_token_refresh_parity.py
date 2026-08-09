"""OAuth-Token-Auffrischung in ALLEN Laufzeiten (#488).

Ein OAuth-Zugriffstoken lebt ein bis zwei Stunden. CUSTOM_MCP_AUTH wird aber nur
EINMAL gesetzt, beim Erstellen des Containers — ein lang laufender Agent verlor damit
jeden OAuth-geschuetzten MCP-Server innerhalb der ersten Stunde.

Orchestrator-Seite (periodischer Lauf) und Endpunkt waren bereits gebaut. Die
Agenten-Schleife auch — aber MIT AUSNAHME von codex_cli, begruendet damit, Codex nutze
CUSTOM_MCP_* gar nicht.

Das stimmt nicht: ``_ensure_codex_mcp_config`` liest CUSTOM_MCP_SERVERS und
CUSTOM_MCP_AUTH aus ``os.environ.copy()`` und schreibt die config.toml bei JEDEM
Codex-Aufruf neu. Auf einer Anlage, auf der sieben von acht Agenten Codex sind, war
der Fix damit fuer fast niemanden wirksam — wieder ein Weg, der an einer vorhandenen
Faehigkeit vorbeigeht.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "agent"
ORCH = REPO / "orchestrator"


class OrchestratorSideTests(unittest.TestCase):
    def test_periodic_sweep_runs(self):
        """Nicht nur beim Bauen eines Containers."""
        src = (ORCH / "app/main.py").read_text()
        self.assertIn("refresh_all_oauth_servers", src)
        self.assertIn("_refresh_mcp_oauth_tokens", src)

    def test_endpoint_exists_for_running_agents(self):
        from app.api import agents

        paths = {r.path for r in agents.router.routes}
        self.assertIn("/agents/{agent_id}/mcp-credentials", paths)


class AgentSideTests(unittest.TestCase):
    SRC = (AGENT / "app/main.py").read_text()

    def test_loop_exists(self):
        self.assertIn("async def refresh_mcp_credentials_loop", self.SRC)

    def test_codex_is_no_longer_excluded(self):
        """Der Kern des Fixes."""
        block = self.SRC.split("Periodic MCP credential refresh")[1].split("# Graceful shutdown")[0]
        self.assertNotIn('mode != "codex_cli" and', block)

    def test_loop_starts_whenever_there_are_custom_servers(self):
        block = self.SRC.split("Periodic MCP credential refresh")[1].split("# Graceful shutdown")[0]
        self.assertIn('if os.environ.get("CUSTOM_MCP_SERVERS")', block)

    def test_codex_does_not_register_via_the_claude_cli(self):
        """Codex verwaltet seine Server ueber die config.toml; ein `claude mcp add`
        waere dort wirkungslos."""
        block = self.SRC.split("Periodic MCP credential refresh")[1].split("# Graceful shutdown")[0]
        self.assertIn('mode not in ("custom_llm", "codex_cli")', block)

    def test_env_is_updated_for_every_mode(self):
        """Das Auffrischen der Umgebung ist der Teil, von dem Codex lebt."""
        block = self.SRC.split("async def refresh_mcp_credentials_loop")[1][:4000]
        self.assertIn('os.environ["CUSTOM_MCP_AUTH"]', block)


class CodexReadsTheEnvTests(unittest.TestCase):
    """Der Beleg, dass die alte Begruendung nicht stimmte."""

    SRC = (AGENT / "app/codex_runner.py").read_text()

    def test_codex_reads_custom_mcp_auth(self):
        self.assertIn('env.get("CUSTOM_MCP_AUTH"', self.SRC)
        self.assertIn('env.get("CUSTOM_MCP_SERVERS"', self.SRC)

    def test_codex_env_is_a_live_copy_of_os_environ(self):
        """Deshalb wirkt eine aufgefrischte Umgebung ueberhaupt."""
        block = self.SRC.split("def _codex_env()")[1][:400]
        self.assertIn("os.environ.copy()", block)

    def test_config_is_rewritten_per_invocation(self):
        """Sonst wuerde selbst eine frische Umgebung nichts aendern."""
        self.assertIn("Called once per Codex invocation", self.SRC)


class OrderingTests(unittest.TestCase):
    def test_servers_are_written_last(self):
        """#502: Jeder Leser nimmt zuerst CUSTOM_MCP_SERVERS und sucht dann die
        passenden Zugangsdaten. Neue Server vor neuen Tokens zu schreiben ergaebe
        einen 401 aus einem halb gelesenen Zustand."""
        block = (AGENT / "app/main.py").read_text().split(
            "async def refresh_mcp_credentials_loop")[1][:4000]
        auth_at = block.index('os.environ["CUSTOM_MCP_AUTH"]')
        servers_at = block.index('os.environ["CUSTOM_MCP_SERVERS"]')
        self.assertLess(auth_at, servers_at)


if __name__ == "__main__":
    unittest.main()
