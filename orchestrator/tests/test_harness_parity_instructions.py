"""Harness-Paritaet: dieselbe Anleitung erreicht JEDEN Harness.

Harte Vorgabe des Nutzers: bei den Harnessen muss ALLES gleich sein. Der Fall, der
dazu gefuehrt hat: die gemeinsame Anleitung wurde als `/workspace/AGENT.md` abgelegt,
die Codex-CLI liest per Konvention aber `AGENTS.md` — der Codex-Agent hat sie also nie
gesehen, samt aller spaeteren Verbesserungen. Und ein Name stand ueberhaupt nicht drin,
weshalb Agenten auf die Frage „wie heisst du?" mit „ich habe keinen Namen" antworteten.

Diese Tests importieren `agent_manager` NICHT (Docker-Abhaengigkeit) und pruefen die
reinen Funktionen bzw. den Quelltext.
"""

import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/core/agent_manager.py").read_text()


def _load_pure_helpers():
    """`instructions_paths` + `_identity_line` isoliert ausfuehrbar machen.

    Beide sind reine Funktionen ohne Importe — wir schneiden sie aus der Quelle und
    fuehren nur sie aus, damit der Test ohne das `docker`-Modul laeuft.
    """
    ns: dict = {}
    start = SRC.index("def instructions_paths(")
    end = SRC.index("def _render_claude_md(")
    exec(compile(SRC[start:end], "<helpers>", "exec"), ns)
    return ns


class InstructionFileTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_pure_helpers()

    def test_every_mode_gets_at_least_one_file(self):
        for mode in ("claude_code", "codex_cli", "codex", "custom_llm", "", None, "etwas_neues"):
            with self.subTest(mode=mode):
                paths = self.ns["instructions_paths"](mode)
                self.assertTrue(paths, f"Modus {mode!r} bekommt gar keine Anleitung")
                for p in paths:
                    self.assertTrue(p.startswith("/workspace/"))

    def test_claude_code_gets_claude_md(self):
        self.assertEqual(self.ns["instructions_paths"]("claude_code"), ["/workspace/CLAUDE.md"])

    def test_codex_gets_the_file_its_cli_actually_reads(self):
        """AGENTS.md (Mehrzahl) ist die Codex-Konvention — genau das fehlte."""
        for mode in ("codex_cli", "codex"):
            paths = self.ns["instructions_paths"](mode)
            self.assertIn("/workspace/AGENTS.md", paths)
            # AGENT.md bleibt zusaetzlich: Werkzeuge und Prompts verweisen darauf.
            self.assertIn("/workspace/AGENT.md", paths)

    def test_unknown_mode_falls_back_to_agent_md(self):
        self.assertEqual(self.ns["instructions_paths"]("was_auch_immer"), ["/workspace/AGENT.md"])

    def test_primary_path_helper_stays_consistent(self):
        for mode in ("claude_code", "codex_cli", "custom_llm"):
            self.assertEqual(
                self.ns["instructions_path"](mode), self.ns["instructions_paths"](mode)[0]
            )


class IdentityInTemplateTests(unittest.TestCase):
    def setUp(self):
        self.ns = _load_pure_helpers()

    def test_identity_line_carries_name_and_role(self):
        line = self.ns["_identity_line"]("Mr. Data", "Datenanalyst")
        self.assertIn("Mr. Data", line)
        self.assertIn("Datenanalyst", line)

    def test_identity_line_survives_missing_pieces(self):
        self.assertIn("Mr. Data", self.ns["_identity_line"]("Mr. Data", ""))
        self.assertTrue(self.ns["_identity_line"]("", "").strip())  # nie leer

    def test_template_has_an_identity_section_and_it_is_filled(self):
        """Die Vorlage muss den Platzhalter haben UND er muss ersetzt werden —
        sonst steht am Ende woertlich '$AGENT_IDENTITY' in der Anleitung."""
        self.assertIn("$AGENT_IDENTITY", SRC.split("def _render_claude_md(")[0])
        render = SRC.split("def _render_claude_md(")[1].split("def _container_slug")[0]
        self.assertIn('.replace("$AGENT_IDENTITY"', render)

    def test_every_write_site_passes_the_agent_name(self):
        """Jede Stelle, die die Anleitung rendert, muss Name UND Rolle mitgeben —
        sonst bekommt genau dieser Pfad wieder eine namenlose Datei."""
        calls = SRC.count("_render_claude_md(")
        # 1x Definition + N Aufrufe; jeder Aufruf traegt agent_name=
        self.assertEqual(SRC.count("agent_name="), calls - 1,
                         "ein _render_claude_md-Aufruf uebergibt keinen agent_name")


if __name__ == "__main__":
    unittest.main()
