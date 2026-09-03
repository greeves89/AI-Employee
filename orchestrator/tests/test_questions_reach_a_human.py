"""Eine Rückfrage muss dort landen, wo jemand sie beantworten kann.

Kundenfall vom 2026-08-13, Auftrag ``tf2tjaazb``: Der Lead delegiert an Mr. Design.
Der antwortet — in zwei Zügen, ohne ein einziges Werkzeug:

    „Bevor ich starte: Für mich sind aktuell keine wiederkehrenden
    Verantwortungsbereiche hinterlegt. Damit ich korrekt weiterarbeiten darf,
    brauche ich einmal deine kurze Festlegung fürs Onboarding.
    […] antworte bitte kurz mit ‚passt'."

Niemand hat geantwortet, weil in einem delegierten Auftrag niemand sitzt. Der Lauf
zählte als abgeschlossen, geliefert wurde nichts. Zwei Fehler stecken darin:

1. **Das Onboarding-Interview lief in einem Auftrag.** Die Anweisung sagte „bei der
   ERSTEN Unterhaltung" — ein Auftrag ist keine Unterhaltung, aber das stand nicht da.
2. **Die Rückfrage war Fließtext.** In einem Chat ist das richtig; in einem Auftrag
   liest sie niemand. Dafür gibt es ``request_approval``: das erreicht den Menschen
   und wartet.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGER = (ROOT / "orchestrator/app/core/agent_manager.py").read_text()


class ThereIsNoOnboardingInterviewAnymoreTests(unittest.TestCase):
    """Ein Agent entsteht aus einer Vorlage — Rolle, Schwerpunkte und Grenzen
    stehen dort bereits. Sie noch einmal abzufragen war ueberfluessig, und in
    einem Auftrag war es schaedlich."""

    KNOWLEDGE = MANAGER.split("DEFAULT_KNOWLEDGE_MD = ")[1].split('"""')[1]

    def test_the_interview_is_gone(self):
        for phrase in ("Onboarding Interview", "Onboarding Status: NOT COMPLETED",
                       "conduct an onboarding interview"):
            with self.subTest(phrase):
                self.assertNotIn(phrase, self.KNOWLEDGE)

    def test_the_template_is_named_as_the_source_of_truth(self):
        self.assertIn("Vorlage", self.KNOWLEDGE)

    def test_missing_setup_does_not_stop_a_task(self):
        self.assertIn("kein Grund, eine Aufgabe anzuhalten", self.KNOWLEDGE)

    def test_it_says_what_to_do_instead(self):
        """Verbieten allein hilft nicht — ohne Ersatzweg bleibt der Agent stehen."""
        self.assertIn("arbeite trotzdem", self.KNOWLEDGE)
        self.assertIn("request_approval", self.KNOWLEDGE)

    def test_a_template_agent_keeps_its_own_knowledge(self):
        """Die leere Vorgabe darf die Vorlagenbeschreibung nicht ueberschreiben."""
        self.assertIn("(knowledge_md or \"\").strip() or DEFAULT_KNOWLEDGE_MD", MANAGER)


class QuestionsInTasksGoThroughApprovalTests(unittest.TestCase):
    def test_the_rule_distinguishes_chat_from_task(self):
        """Vorher galt EINE Regel fuer beides — und die war fuer den Auftrag falsch."""
        self.assertIn("In a chat with a human:", MANAGER)
        self.assertIn("In a task, a delegated job, or a proactive run", MANAGER)

    def test_it_names_the_working_channel(self):
        block = MANAGER.split("Asking the user something")[1][:1400]
        self.assertIn("request_approval", block)

    def test_it_says_plainly_that_text_reaches_nobody(self):
        self.assertIn("NOBODY READS YOUR ANSWER TEXT", MANAGER)

    def test_the_agent_is_told_to_deliver_anyway(self):
        """Der teuerste Teil des Vorfalls war nicht die Frage, sondern dass gar
        nichts geliefert wurde."""
        block = MANAGER.split("Asking the user something")[1][:1400]
        self.assertIn("safest reasonable default", block)


class TheRuleReachesEveryRuntimeTests(unittest.TestCase):
    """Die Anleitung wird aus EINER Quelle in alle drei Dateien geschrieben —
    CLAUDE.md, AGENTS.md, AGENT.md. Steht die Regel im gemeinsamen Text, haben
    alle drei sie."""

    def test_the_rule_lives_in_the_shared_template(self):
        shared = MANAGER.split("DEFAULT_CLAUDE_MD = ")[1].split("DEFAULT_KNOWLEDGE_MD")[0]
        self.assertIn("NOBODY READS YOUR ANSWER TEXT", shared)

    def test_all_three_files_are_written(self):
        from app.core.agent_manager import instructions_paths

        self.assertIn("/workspace/CLAUDE.md", instructions_paths("claude_code"))
        self.assertIn("/workspace/AGENTS.md", instructions_paths("codex_cli"))
        self.assertIn("/workspace/AGENT.md", instructions_paths("custom_llm"))


if __name__ == "__main__":
    unittest.main()
