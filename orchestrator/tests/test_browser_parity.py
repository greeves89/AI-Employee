"""Browser-Steuerung in ALLEN Laufzeiten (Harness-Paritaet).

Playwright war laengst im Agenten-Image, und `main.py` registriert es per
`claude mcp add`. Genau darin lag die Luecke: `claude mcp add` schreibt in die
Konfiguration der Claude-CLI, und die lesen Codex und Custom-LLM nicht. Von drei
Harnessen konnte also nur einer im Browser arbeiten — und eine Faehigkeit gilt hier
erst als vorhanden, wenn sie ueberall vorhanden ist.

Der Roadmap-Punkt „Browser-Automatisierung als Tool" war deshalb halb erledigt: die
Faehigkeit war da, aber zwei Wege gingen daran vorbei.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "agent"


class ClaudePathTests(unittest.TestCase):
    def test_claude_still_gets_the_mcp(self):
        """Der bestehende Weg bleibt — es wird nichts ersetzt, nur ergaenzt."""
        src = (AGENT / "app/main.py").read_text()
        self.assertIn("@playwright/mcp", src)
        self.assertIn("COMPUTER_USE_BROWSER", src)


class SharedProfileTests(unittest.TestCase):
    """Die bestehenbleibende Anmeldung muss in ALLEN Laufzeiten gelten (#828).

    Der Nutzer meldet sich einmal selbst an, der Agent arbeitet danach in
    derselben Sitzung weiter. Haengt das am Profil EINER Laufzeit, gilt es
    fuer die anderen nicht — und die Faehigkeit waere nur halb da.
    """

    def test_python_tool_uses_a_persistent_profile(self):
        src = (AGENT / "app/tools/browser.py").read_text()
        self.assertIn(
            "launch_persistent_context", src,
            "Ohne persistenten Kontext startet jeder Lauf abgemeldet.",
        )

    def test_mcp_gets_the_same_profile(self):
        """Sonst haette Claude Code ein eigenes Profil und waere abgemeldet."""
        src = (AGENT / "app/main.py").read_text()
        self.assertIn("--user-data-dir", src)
        self.assertIn("from app.tools.browser import PROFIL_DIR", src,
                      "Der Pfad darf nicht zweimal getippt werden — sonst laufen "
                      "die beiden Profile irgendwann auseinander.")

    def test_tabs_are_declared_everywhere(self):
        deklaration = (AGENT / "app/tools/definitions.py").read_text()
        for aktion in ("new_tab", "list_tabs", "switch_tab", "close_tab"):
            self.assertIn(aktion, deklaration,
                          f"'{aktion}' fehlt in der Werkzeugbeschreibung")
            self.assertIn(aktion, (AGENT / "app/tools/browser.py").read_text(),
                          f"'{aktion}' ist beschrieben, aber nicht umgesetzt")


class OtherRuntimesTests(unittest.TestCase):
    def test_tool_is_declared_for_codex_and_custom_llm(self):
        src = (AGENT / "app/tools/definitions.py").read_text()
        self.assertIn('"name": "browser"', src)

    def test_tool_is_implemented(self):
        src = (AGENT / "app/tools/api_client.py").read_text()
        self.assertIn("async def browser", src)

    def test_implementation_exists(self):
        self.assertTrue((AGENT / "app/tools/browser.py").exists())

    def test_reachable_without_search_tools(self):
        """Sonst weicht das Modell auf bash/curl aus und bekommt HTML ohne
        JavaScript-Inhalt."""
        src = (AGENT / "app/llm_chat_handler.py").read_text()
        core = src.split("CORE_TOOL_NAMES = {")[1].split("}")[0]
        self.assertIn('"browser"', core)

    def test_dependency_is_declared(self):
        self.assertIn("playwright", (AGENT / "pyproject.toml").read_text())

    def test_chromium_is_already_in_the_image(self):
        """Nur die Steuerung kommt dazu, nicht der ganze Browser-Stapel."""
        self.assertIn("chromium", (AGENT / "Dockerfile").read_text())


class ToolShapeTests(unittest.TestCase):
    SRC = AGENT / "app/tools/browser.py"

    def test_one_tool_with_actions_not_twelve(self):
        """Der Werkzeugkatalog ist auf 128 Eintraege begrenzt; zwoelf
        Browser-Eintraege haetten davon ein Zehntel verbraucht."""
        defs = (AGENT / "app/tools/definitions.py").read_text()
        self.assertEqual(defs.count('"name": "browser"'), 1)
        self.assertIn('"action"', defs.split('"name": "browser"')[1][:2000])

    def test_errors_are_named_not_swallowed(self):
        """Sagt das Werkzeug nur 'hat nicht geklappt', weicht das Modell auf bash aus."""
        src = self.SRC.read_text()
        self.assertIn("failed:", src)
        self.assertIn("unknown action", src)

    def test_output_is_bounded(self):
        src = self.SRC.read_text()
        self.assertIn("MAX_TEXT_CHARS", src)

    def test_browser_is_reused_across_calls(self):
        """Chromium zu starten dauert ein bis zwei Sekunden, und ein Ablauf besteht
        fast immer aus mehreren Schritten auf derselben Seite."""
        src = self.SRC.read_text()
        self.assertIn("_ensure_page", src)
        self.assertIn("close_browser", src)

    def test_delimits_itself_from_the_user_desktop(self):
        """computer_use steuert den Bildschirm des Nutzers, dieses hier einen Browser
        im Container — ohne klare Abgrenzung nimmt das Modell das falsche."""
        defs = (AGENT / "app/tools/definitions.py").read_text()
        block = defs.split('"name": "browser"')[1][:2000]
        self.assertIn("computer_use", block)


if __name__ == "__main__":
    unittest.main()
