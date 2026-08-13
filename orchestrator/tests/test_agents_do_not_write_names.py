"""Die Agenten lernen dieselbe Regel wie wir: keine Namen ins Repository.

Am 2026-08-13 stand der Name eines Kunden an 34 Stellen im oeffentlichen Repo —
geschrieben von Menschen. Die Agenten schreiben inzwischen selbst Code, Commits,
Pull Requests und Issues; ohne diese Regel machen sie denselben Fehler, nur
schneller und oefter.

Der Anweisungsblock steht in ``DEFAULT_CLAUDE_MD`` und damit in ``/workspace/``
JEDES Agenten — modusuebergreifend, also fuer Claude Code, Codex und Custom-LLM
gleichermassen. Eine Regel, die nur eine Laufzeit erreicht, ist keine.
"""

import unittest

from app.core.agent_manager import DEFAULT_CLAUDE_MD


class TheRuleIsInTheInstructionsTests(unittest.TestCase):
    def test_it_forbids_customer_company_and_person_names(self):
        self.assertIn("Kunden, einer Firma oder einer Person", DEFAULT_CLAUDE_MD)

    def test_it_covers_more_than_source_code(self):
        """Commit, Pull Request und Issue sind genauso oeffentlich wie Code."""
        for ort in ("Commit-Nachricht", "Pull Request", "Issue", "CHANGELOG"):
            with self.subTest(ort=ort):
                self.assertIn(ort, DEFAULT_CLAUDE_MD)

    def test_it_says_what_to_write_instead(self):
        """Ein Verbot ohne Alternative wird umgangen oder laehmt."""
        self.assertIn("beim Kunden", DEFAULT_CLAUDE_MD)
        self.assertIn("eine Kundenanlage", DEFAULT_CLAUDE_MD)

    def test_it_names_the_reserved_example_domains(self):
        self.assertIn("example.com", DEFAULT_CLAUDE_MD)
        self.assertIn("example.invalid", DEFAULT_CLAUDE_MD)

    def test_it_explains_why_it_happens_by_accident(self):
        """Ohne das Warum liest sich die Regel wie Buerokratie und wird ignoriert."""
        self.assertIn("beilaeufig", DEFAULT_CLAUDE_MD)

    def test_it_says_where_the_real_name_belongs(self):
        """Sonst verlieren die Agenten Wissen, das sie brauchen."""
        self.assertIn("save_memory", DEFAULT_CLAUDE_MD)

    def test_a_private_repo_is_no_exception(self):
        """„Ist doch privat" war genau die Begruendung, mit der es entstand."""
        self.assertIn("auch wenn das Repo heute privat ist", DEFAULT_CLAUDE_MD)


class TheInstructionsReachEveryAgentTests(unittest.TestCase):
    """Ein Text, den niemand ausgeliefert bekommt, aendert nichts — dieselbe
    Falle wie beim entfernten Onboarding-Abschnitt."""

    def test_the_rendered_file_carries_it(self):
        """``_render_claude_md`` ist die EINE Stelle, die die Anleitung baut —
        Anlegen, Update und Neustart gehen alle dort durch."""
        from app.core.agent_manager import _render_claude_md

        text = _render_claude_md([], None, agent_name="Testi", agent_role="Test")
        self.assertIn("Kunden, einer Firma oder einer Person", text)
        self.assertIn("beim Kunden", text)

    def test_existing_agents_get_it_refreshed(self):
        """Sonst behalten laufende Agenten ihre alte Anleitung — die Datei liegt
        im Container, nicht im Repo."""
        import inspect

        from app.core.agent_manager import AgentManager

        src = inspect.getsource(AgentManager.refresh_instructions)
        self.assertIn("_render_claude_md(", src)
        self.assertIn("write_file_in_container", src)


if __name__ == "__main__":
    unittest.main()
