"""Bestandsagenten verlieren den überholten Onboarding-Abschnitt — aber nichts sonst.

Das Interview ist entfallen (der Agent hält sich an seine Vorlage). Für **neue**
Agenten genügte dafür die geänderte Standardvorlage. Bestandsagenten aber tragen
ihre ``knowledge.md`` im eigenen Volume, und die überlebt jedes Neuerstellen —
absichtlich, denn dort steht Gelerntes.

Auf der Kundenanlage stand deshalb nach dem Umbau immer noch:

    ## Onboarding Status: NOT COMPLETED
    **IMPORTANT: On my FIRST conversation, I MUST conduct an onboarding interview!**

Ein Agent mit dieser Zeile hält weiterhin Aufträge an, um nach seiner Rolle zu
fragen — genau der Fall, der am 2026-08-13 einen delegierten Auftrag ohne Ergebnis
zurückkommen liess.

Die Migration ersetzt deshalb **nur den Kopf**. Alles ab dem ersten Abschnitt, den
der Agent selbst gefüllt haben könnte, bleibt Zeichen für Zeichen stehen. Ein
Wissensspeicher, den eine Migration ausräumt, wäre teurer als das Problem.
"""

import unittest

from app.core.agent_manager import DEFAULT_KNOWLEDGE_MD, strip_onboarding_block

ALT = """# Agent Knowledge Base

## Onboarding Status: NOT COMPLETED
**IMPORTANT: On my FIRST conversation, I MUST conduct an onboarding interview!**

### Onboarding Interview Steps:
1. Introduce myself
2. Ask the user: "What role should I fill?"

## My Role
UI/UX Designer fuer klinische Anwendungen

## Learned Patterns
- Kontrastpruefung immer gegen WCAG AA, nicht nach Gefuehl
- Der Kunde nennt sein Haus konsequent "skbs", klein geschrieben

## Errors & Fixes
- Logo nie nachzeichnen — nur verifizierte Originaldatei
"""


class TheOnboardingBlockGoesTests(unittest.TestCase):
    def test_it_is_removed(self):
        out = strip_onboarding_block(ALT)
        self.assertIsNotNone(out)
        self.assertNotIn("Onboarding Status: NOT COMPLETED", out)
        self.assertNotIn("Onboarding Interview Steps", out)

    def test_the_new_head_is_there(self):
        out = strip_onboarding_block(ALT)
        self.assertIn("Vorlage", out)
        self.assertIn("kein Grund, eine Aufgabe anzuhalten", out)


class LearnedContentSurvivesTests(unittest.TestCase):
    """Der teuerste denkbare Fehler an dieser Stelle."""

    def test_learned_patterns_survive_verbatim(self):
        out = strip_onboarding_block(ALT)
        self.assertIn("Kontrastpruefung immer gegen WCAG AA", out)
        self.assertIn('konsequent "skbs", klein geschrieben', out)

    def test_errors_and_fixes_survive(self):
        out = strip_onboarding_block(ALT)
        self.assertIn("Logo nie nachzeichnen", out)

    def test_a_filled_role_survives(self):
        """``## My Role`` gehoert dem Agenten, sobald er es gefuellt hat."""
        out = strip_onboarding_block(ALT)
        self.assertIn("UI/UX Designer fuer klinische Anwendungen", out)


class NothingElseIsTouchedTests(unittest.TestCase):
    def test_a_clean_file_is_left_alone(self):
        """Zweimal migrieren darf nicht zweimal etwas aendern."""
        self.assertIsNone(strip_onboarding_block(DEFAULT_KNOWLEDGE_MD))

    def test_an_already_migrated_file_is_left_alone(self):
        once = strip_onboarding_block(ALT)
        self.assertIsNone(strip_onboarding_block(once))

    def test_an_empty_file_is_left_alone(self):
        self.assertIsNone(strip_onboarding_block(""))

    def test_a_file_without_any_known_section_still_loses_the_block(self):
        text = ("# Agent Knowledge Base\n\n## Onboarding Status: NOT COMPLETED\n"
                "**IMPORTANT: interview!**\n")
        out = strip_onboarding_block(text)
        self.assertNotIn("Onboarding Status: NOT COMPLETED", out)


class TheMigrationRunsOnRecreateTests(unittest.TestCase):
    """Ein Helfer, den niemand ruft, aendert nichts — dieselbe Falle wie bei den
    eigenen Abo-Zugaengen, die einen halben Tag ungenutzt im Baum lagen."""

    def test_it_is_wired_into_the_container_refresh(self):
        import inspect

        from app.core import agent_manager

        src = inspect.getsource(agent_manager)
        self.assertIn("migrated = strip_onboarding_block(", src)


if __name__ == "__main__":
    unittest.main()
