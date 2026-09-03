"""Eine Ankündigung ist keine erledigte Aufgabe.

Am 2026-08-12 standen auf der Kundenanlage zwei delegierte Aufträge auf
**COMPLETED**, in deren Ergebnis wörtlich steht:

    „Ich habe inhaltlich noch **keine** Repo-Änderungen umgesetzt (nur
    angekündigt)."

Der Hergang, ablesbar an ``num_turns = 3`` und genau einem Werkzeugaufruf:

1. Der Agent antwortet mit reiner Prosa — kein einziger Werkzeugaufruf.
2. Das Abschluss-Gatter prüfte nur: „wurde ``rate_task`` gerufen?" → nein →
   „[SYSTEM] You are about to finish, but you still MUST: call rate_task".
3. Der Agent ruft ``rate_task``. Das ist der einzige Werkzeugaufruf des Laufs.
4. Nächster Zug ohne Werkzeuge, Nudge verbraucht → Lauf endet als *completed*.

Das Gatter hat die Ankündigung damit nicht nur durchgelassen, sondern sie in
einen Erfolg hineingerettet. Der Team-Lead meldete anschließend wahrheitsgemäß
„erledigt" — der Task stand ja so da. Für den Betrachter arbeitete niemand,
während alle Aufträge grün waren.

Die Reihenfolge ist der ganze Punkt: **erst die Arbeit, dann die Buchhaltung.**
"""

import unittest

from app.llm_runner import LLMRunner


class SubstantiveWorkTests(unittest.TestCase):
    def test_only_the_closing_ritual_is_not_work(self):
        self.assertFalse(LLMRunner._did_substantive_work({"rate_task"}))

    def test_thinking_about_oneself_is_not_work(self):
        """Gedächtnis durchsuchen, Fertigkeiten suchen, Todos ansehen — nichts
        davon rührt die Aufgabe an. Genau diese Kette stand in beiden
        Fehlläufen vor der Ankündigung."""
        self.assertFalse(LLMRunner._did_substantive_work({
            "brain_search", "memory_search", "skill_search", "list_todos",
            "rate_task",
        }))

    def test_reading_a_file_is_work(self):
        """Eine reine Lese-Prüfung ist eine vollwertige Aufgabe — sie darf nicht
        als 'nichts getan' gelten."""
        self.assertTrue(LLMRunner._did_substantive_work({"read_file", "rate_task"}))

    def test_writing_is_work(self):
        self.assertTrue(LLMRunner._did_substantive_work({"write_file"}))

    def test_asking_for_approval_alone_is_not_work(self):
        """Eine Rückfrage ist eine Rückfrage, kein Ergebnis."""
        self.assertFalse(LLMRunner._did_substantive_work({"request_approval"}))


class ComplianceGateTests(unittest.TestCase):
    def test_work_is_demanded_before_bookkeeping(self):
        gaps = LLMRunner._compliance_gaps(set(), lightweight=False)
        self.assertEqual(len(gaps), 1,
                         "Bei nicht getaner Arbeit darf NUR die Arbeit gefordert "
                         "werden — sonst holt der Agent den Papierkram nach und "
                         "der Lauf gilt als erledigt")
        self.assertIn("actually DO the task", gaps[0])
        self.assertNotIn("rate_task", gaps[0])

    def test_the_exact_failure_case(self):
        """Der Werkzeugsatz beider Fehlläufe, bevor das Gatter zuschlug."""
        called = {"read_file", "brain_search", "memory_search", "skill_search",
                  "list_todos"}
        # read_file zaehlt als Arbeit — deshalb hier ohne, wie im echten Lauf:
        called.discard("read_file")
        gaps = LLMRunner._compliance_gaps(called, lightweight=False)
        self.assertIn("actually DO the task", gaps[0])

    def test_after_real_work_the_rating_is_demanded(self):
        gaps = LLMRunner._compliance_gaps({"write_file", "bash"}, lightweight=False)
        self.assertEqual(gaps, ["call rate_task to record this task's quality"])

    def test_a_complete_run_owes_nothing(self):
        self.assertEqual(
            LLMRunner._compliance_gaps({"write_file", "rate_task"}, lightweight=False),
            [],
        )

    def test_chat_style_runs_are_left_alone(self):
        """Auf eine Frage zu antworten ist kein Auftrag — hier darf nichts
        eingefordert werden, auch keine Arbeit."""
        self.assertEqual(LLMRunner._compliance_gaps(set(), lightweight=True), [])

    def test_installed_skills_still_get_rated(self):
        gaps = LLMRunner._compliance_gaps(
            {"skill_install", "bash", "rate_task"}, lightweight=False)
        self.assertEqual(gaps, ["call skill_rate for the marketplace skill you installed"])


if __name__ == "__main__":
    unittest.main()
