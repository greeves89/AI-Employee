"""Der Werkzeug-Katalog darf nicht von der Wirklichkeit abweichen.

``core/agent_toolset.py`` beschreibt, was ein Agent je Laufzeit kann — für die
Befehlsliste im Chat. Es ist ein **Katalog**, kein Live-Abbild: der Orchestrator
sieht ``agent/`` nicht (nicht in seinen Container gemountet), und den Agenten zu
fragen ginge nur, solange er läuft.

Der Preis dafür ist Abweichung, und dieser Test ist die Gegenmassnahme: er liest
die echten Werkzeugdefinitionen aus dem Repo und hält sie gegen den Katalog. Wer
ein Werkzeug hinzufügt und den Katalog vergisst, merkt es hier — nicht der Nutzer
an einer Liste, in der etwas fehlt.

Dasselbe Muster wie ``test_confidence_parity.py``.
"""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.core.agent_toolset import (
    CLAUDE_CODE_BUILTINS,
    DEFINITION_TOOLS,
    MCP_SERVER_TOOLS,
    PLATFORM_COMMANDS,
    toolset_for,
)

ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = ROOT / "agent/mcp"
DEFINITIONS = ROOT / "agent/app/tools/definitions.py"


def _tools_in_mjs(path: Path) -> set[str]:
    return set(re.findall(r'^\s{4,8}name:\s*"([a-z0-9_]+)"', path.read_text(), re.M))


def _tools_in_definitions() -> set[str]:
    return set(re.findall(r'"name":\s*"([a-z0-9_]+)"', DEFINITIONS.read_text()))


class DefinitionParityTests(unittest.TestCase):
    """Codex und Custom-LLM lesen dieselbe Datei — der Katalog muss sie treffen."""

    def test_no_tool_is_missing_from_the_catalogue(self):
        actual = _tools_in_definitions()
        missing = actual - set(DEFINITION_TOOLS)
        self.assertEqual(
            missing, set(),
            "Neue Werkzeuge in definitions.py, die im Katalog fehlen — sie tauchen "
            "in der Befehlsliste nicht auf: " + ", ".join(sorted(missing)),
        )

    def test_the_catalogue_invents_nothing(self):
        actual = _tools_in_definitions()
        phantom = set(DEFINITION_TOOLS) - actual
        self.assertEqual(
            phantom, set(),
            "Der Katalog nennt Werkzeuge, die es nicht (mehr) gibt — die Liste "
            "verspricht dann etwas, das ins Leere läuft: " + ", ".join(sorted(phantom)),
        )


class McpParityTests(unittest.TestCase):
    """Claude Code bekommt seine Werkzeuge über die MCP-Server."""

    def test_every_catalogued_server_exists(self):
        for server in MCP_SERVER_TOOLS:
            with self.subTest(server=server):
                self.assertTrue(
                    (MCP_DIR / f"{server}-server.mjs").exists(),
                    f"Katalog kennt einen MCP-Server {server}, den es nicht gibt",
                )

    def test_no_tool_is_missing_per_server(self):
        for server, catalogued in MCP_SERVER_TOOLS.items():
            path = MCP_DIR / f"{server}-server.mjs"
            if not path.exists():
                continue
            with self.subTest(server=server):
                missing = _tools_in_mjs(path) - set(catalogued)
                self.assertEqual(
                    missing, set(),
                    f"{server}: neue Werkzeuge, die im Katalog fehlen: "
                    + ", ".join(sorted(missing)),
                )

    def test_the_catalogue_invents_no_tools(self):
        for server, catalogued in MCP_SERVER_TOOLS.items():
            path = MCP_DIR / f"{server}-server.mjs"
            if not path.exists():
                continue
            with self.subTest(server=server):
                phantom = set(catalogued) - _tools_in_mjs(path)
                self.assertEqual(
                    phantom, set(),
                    f"{server}: Katalog nennt Werkzeuge, die es nicht gibt: "
                    + ", ".join(sorted(phantom)),
                )

    def test_a_new_mcp_server_does_not_go_unnoticed(self):
        """Ein Server im Verzeichnis, der im Katalog fehlt, waere unsichtbar."""
        on_disk = {
            p.name.replace("-server.mjs", "").replace(".mjs", "")
            for p in MCP_DIR.glob("*.mjs")
            if _tools_in_mjs(p)
        }
        # bash-approval und die HTTP-Variante von hyperframes tragen keine eigene
        # Werkzeugliste bzw. doppeln eine vorhandene.
        on_disk -= {"hyperframes-server-http"}
        missing = on_disk - set(MCP_SERVER_TOOLS)
        self.assertEqual(missing, set(), "Nicht katalogisierte MCP-Server: " + ", ".join(sorted(missing)))


class ShapeTests(unittest.TestCase):
    def _agent(self, mode="claude_code", **config):
        return SimpleNamespace(mode=mode, config=config)

    def test_claude_code_sees_its_own_tools_and_the_mcp_servers(self):
        out = toolset_for(self._agent("claude_code"))
        labels = [g["label"] for g in out["groups"]]
        self.assertIn("Claude Code", labels)
        self.assertTrue(any(l.startswith("MCP · ") for l in labels))
        # NICHT den Codex-Satz: der Agent hat ihn nicht.
        self.assertNotIn("Codex · Werkzeuge", labels)

    def test_codex_sees_the_definition_set_not_the_mcp_servers(self):
        out = toolset_for(self._agent("codex_cli"))
        labels = [g["label"] for g in out["groups"]]
        self.assertIn("Codex · Werkzeuge", labels)
        self.assertFalse(any(l.startswith("MCP · ") for l in labels))

    def test_a_claude_agent_on_the_codex_provider_counts_as_codex(self):
        """Dieselbe Umschreibung wie im Agent-Manager. Stuende sie hier anders,
        zeigte die Liste Werkzeuge, die der Agent gar nicht hat."""
        out = toolset_for(self._agent("claude_code", model_provider="codex"))
        self.assertEqual(out["mode"], "codex_cli")
        self.assertIn("Codex · Werkzeuge", [g["label"] for g in out["groups"]])

    def test_custom_llm_gets_the_same_set_as_codex(self):
        """Harness-Paritaet: eine Faehigkeit ist in beiden da oder in keinem."""
        codex = toolset_for(self._agent("codex_cli"))
        custom = toolset_for(self._agent("custom_llm"))
        self.assertEqual(
            codex["groups"][0]["tools"], custom["groups"][0]["tools"]
        )

    def test_skills_and_custom_mcp_are_appended(self):
        out = toolset_for(
            self._agent("custom_llm"), skills=["pdf_fill"], extra_mcp=["mcp_wiki_search"]
        )
        labels = [g["label"] for g in out["groups"]]
        self.assertIn("Installierte Skills", labels)
        self.assertIn("Eigene MCP-Server", labels)

    def test_nothing_is_shown_that_was_not_asked_for(self):
        """Ohne Skills keine leere Skill-Gruppe — eine leere Ueberschrift sieht
        aus wie ein Fehler."""
        out = toolset_for(self._agent("custom_llm"))
        self.assertNotIn("Installierte Skills", [g["label"] for g in out["groups"]])

    def test_platform_commands_are_everywhere(self):
        """Sie laufen NICHT im Agenten, sondern auf dem gespeicherten Verlauf —
        deshalb koennen sie in allen drei Laufzeiten gleich sein."""
        for mode in ("claude_code", "codex_cli", "custom_llm"):
            with self.subTest(mode=mode):
                names = {c["name"] for c in toolset_for(self._agent(mode))["commands"]}
                for command, _hint in PLATFORM_COMMANDS:
                    self.assertIn(command, names)

    def test_claude_own_commands_are_marked_as_not_ours(self):
        """Sie laufen in der CLI und sind aus dem kopflosen Betrieb nicht
        ausloesbar. Sie zu zeigen ist ehrlich; sie als unsere auszugeben nicht."""
        out = toolset_for(self._agent("claude_code"))
        runtime_only = [c for c in out["commands"] if c.get("runtime_only")]
        self.assertTrue(runtime_only)
        # Und NICHT bei den anderen: dort gibt es sie gar nicht.
        for mode in ("codex_cli", "custom_llm"):
            with self.subTest(mode=mode):
                others = toolset_for(self._agent(mode))["commands"]
                self.assertFalse([c for c in others if c.get("runtime_only")])

    def test_the_count_matches_the_groups(self):
        out = toolset_for(self._agent("claude_code"))
        self.assertEqual(out["total"], sum(len(g["tools"]) for g in out["groups"]))
        self.assertGreater(out["total"], len(CLAUDE_CODE_BUILTINS))


if __name__ == "__main__":
    unittest.main()


class ContextWindowTests(unittest.TestCase):
    """Unbekannt heisst unbekannt.

    Der Anlass steht im Betrieb: auf dem Pi laeuft ``claude-sonnet-5``, und das
    stand in keiner Tabelle — die Kontextanzeige behauptete daraufhin ein
    128k-Fenster. Eine erfundene Zahl ist hier schlimmer als ein ehrliches „?":
    sie verspricht Luft, die es vielleicht nicht gibt, oder sie draengt zum
    Verdichten, wo gar kein Grund ist.
    """

    def test_a_known_model_resolves(self):
        from app.core.agent_toolset import context_window_for

        self.assertEqual(context_window_for("gpt-4o-2024-08-06"), 128_000)

    def test_the_longest_match_wins(self):
        """Sonst landet gpt-4o bei gpt-4 und bekommt 8k statt 128k."""
        from app.core.agent_toolset import context_window_for

        self.assertEqual(context_window_for("gpt-4o"), 128_000)
        self.assertEqual(context_window_for("gpt-4"), 8_192)

    def test_an_unknown_model_is_none_not_a_guess(self):
        from app.core.agent_toolset import context_window_for

        # ``claude-sonnet-5`` stand hier frueher als Beispiel — es WAR unbekannt,
        # und genau deshalb behauptete die Anzeige ein 128k-Fenster. Seit die
        # 5er-Familie eingetragen ist, taugt es nicht mehr als Beispiel; die
        # Regel selbst gilt unveraendert.
        # Bewusst ohne Teilstueck eines bekannten Namens: "gpt-42-turbo"
        # taugt NICHT, dort steckt "gpt-4" drin und trifft zu Recht.
        for model in ("irgendwas-neues", "unbekanntes-modell", "", None):
            with self.subTest(model=model):
                self.assertIsNone(context_window_for(model))

    def test_the_claude_5_family_is_known(self):
        """Nachgetragen aus der Anthropic-Doku (geprueft 2026-08-11): Opus 5,
        Sonnet 5, Fable 5 und Mythos 5 haben 1M."""
        from app.core.agent_toolset import context_window_for

        for model in ("claude-opus-5", "claude-sonnet-5",
                      "claude-fable-5", "claude-mythos-5"):
            with self.subTest(model=model):
                self.assertEqual(context_window_for(model), 1_000_000)

    def test_opus_4_6_has_a_million_not_two_hundred_thousand(self):
        """Stand falsch in der Tabelle. Eine zu KLEIN angegebene Fenstergroesse
        draengt zum Verdichten, wo reichlich Platz ist."""
        from app.core.agent_toolset import context_window_for

        self.assertEqual(context_window_for("claude-opus-4-6"), 1_000_000)

    def test_the_five_family_does_not_shadow_the_four_five_models(self):
        """Laengster Treffer gewinnt — sonst bekaeme claude-sonnet-4-5 das
        1M-Fenster von claude-sonnet-5 untergeschoben."""
        from app.core.agent_toolset import context_window_for

        self.assertEqual(context_window_for("claude-sonnet-4-5"), 200_000)
        self.assertEqual(context_window_for("claude-haiku-4-5"), 200_000)
        self.assertEqual(context_window_for("claude-opus-4-5"), 200_000)

    def test_the_agent_side_still_falls_back(self):
        """Dort ist ein Rueckfallwert richtig: zu frueh zu verdichten kostet einen
        Zusammenfassungsaufruf, zu spaet kostet den Lauf."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2]
               / "agent/app/model_registry.py").read_text()
        self.assertIn("DEFAULT_CONTEXT_WINDOW", src)
