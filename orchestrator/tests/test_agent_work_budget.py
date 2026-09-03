"""Arbeitsbudget und Rueckfrage-Schwelle — je Laufzeit nach ihrem Anbieter.

Ausgangspunkt (19.08.2026): „die agents machen nicht mehr sauber mit … der chat
hier ist deutlich besser". Gemessen an den Selbstbewertungen der Anlage:

    Note 5  ->   1 und 10 Zuege
    Note 4  ->   7, 8, 9, 14, 17 Zuege
    Note 3  ->  14, 27, 75 Zuege

Je laenger der Lauf ohne Korrektur, desto schlechter das Ergebnis. Der Vorschlag
des Nutzers war, ``max_turns`` zu senken. Beide Anbieter widersprechen dem:

* **Anthropic** unterscheidet ausdruecklich einen Deckel, „the model is not aware
  of", von einem Budget, mit dem das Modell „paces itself and finishes gracefully
  instead of being cut off".
* **OpenAI** wirft bei ``max_turns`` eine Ausnahme und empfiehlt, sie aufzufangen
  und um Eingrenzung zu bitten: „I couldn't finish within the turn limit. Please
  narrow the request."

Ein kleinerer Deckel schneidet also frueher ab, statt besser zu werden.
"""

import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
HARNESS = (WURZEL / "agent/app/llm_chat_handler.py").read_text()
MANAGER = (WURZEL / "orchestrator/app/core/agent_manager.py").read_text()


class TheModelKnowsItsBudgetTests(unittest.TestCase):
    """Anthropics Kernaussage: ein Deckel, von dem das Modell nichts weiss,
    kappt es; ein Budget, das es kennt, laesst es sich einteilen."""

    def test_the_remaining_turns_are_told_to_the_model(self):
        self.assertIn("Hinweis zum Arbeitsbudget", HARNESS)
        self.assertIn("BUDGET_WARNUNG_AB", HARNESS)

    def test_it_is_not_announced_from_the_first_turn(self):
        """Von Beginn an waere es Laerm — und wuerde kurze Aufgaben unnoetig
        hetzen."""
        self.assertLess(0.5, float(HARNESS.split("BUDGET_WARNUNG_AB = ", 1)[1].split("\n")[0]))

    def test_it_is_not_repeated_every_turn(self):
        self.assertIn("BUDGET_WARNUNG_ALLE", HARNESS)


class TheRunEndsGracefullyTests(unittest.TestCase):
    """Vorher endete die Schleife bei ``max_turns`` STILL — der Nutzer bekam
    abgebrochene Arbeit, ohne zu erfahren, was fertig war."""

    def test_the_limit_triggers_a_closing_step(self):
        self.assertIn("_abschluss_erbitten", HARNESS)

    def test_the_closing_only_runs_when_the_budget_ran_out(self):
        """`while/else` laeuft nur, wenn die Schleife NICHT per break endete —
        also genau dann, wenn der Agent nicht selbst fertig wurde."""
        import ast
        baum = ast.parse(HARNESS)
        schleifen = [n for n in ast.walk(baum) if isinstance(n, ast.While) and n.orelse]
        self.assertTrue(schleifen, "kein while/else — der Abschluss laeuft auch nach normalem Ende")

    def test_the_closing_runs_without_tools(self):
        """Mit Werkzeugen wuerde der Agent weiterarbeiten — und das Budget ist
        ja gerade der Grund, warum er aufhoeren soll."""
        block = HARNESS.split("async def _abschluss_erbitten", 1)[1][:1600]
        self.assertIn("stream_completion(self._history, [])", block)

    def test_it_asks_for_done_open_and_next(self):
        block = HARNESS.split("async def _abschluss_erbitten", 1)[1][:1600]
        for wort in ("FERTIG", "OFFEN", "naechste Schritt"):
            with self.subTest(teil=wort):
                self.assertIn(wort, block)

    def test_a_failing_closing_does_not_swallow_the_work(self):
        """Ein Fehler beim Abschluss darf nicht auch noch das kosten, was der
        Agent geschafft hat."""
        block = HARNESS.split("async def _abschluss_erbitten", 1)[1][:1900]
        self.assertIn("except Exception", block)


class AskingIsBoundToReversibilityTests(unittest.TestCase):
    """Anthropic nennt das Kriterium beim Namen: „Reversibility is a useful
    criterion: hard-to-reverse actions … can be gated behind user confirmation."

    Vorher hing es an der gefuehlten Unsicherheit des Agenten — und der ist ein
    schlechter Richter darueber: am 18.08.2026 schrieb er „am wahrscheinlichsten"
    und schickte trotzdem drei Kollegen auf das falsche Projekt.
    """

    def test_the_instructions_name_reversibility(self):
        self.assertIn("REVERSIBLE", MANAGER)

    def test_easy_to_undo_means_carry_on(self):
        block = MANAGER.split("Which way to go", 1)[1][:1200]
        self.assertIn("Easy to undo", block)
        self.assertIn("keep going", block)

    def test_hard_to_undo_means_ask_first(self):
        block = MANAGER.split("Which way to go", 1)[1][:1200]
        self.assertIn("Hard to undo", block)
        self.assertIn("request_approval", block)

    def test_delegation_counts_as_hard_to_undo(self):
        """Genau der Fall, der schiefging."""
        block = MANAGER.split("Which way to go", 1)[1][:1200]
        self.assertIn("delegating work to a colleague", block)


class EachRuntimeGetsItsVendorsAdviceTests(unittest.TestCase):
    """Der uebrige Anleitungstext bleibt bewusst fuer alle gleich — Paritaet
    entsteht durch EINE Quelle. Unterschiedlich ist nur, wie die Laufzeit ihre
    Schleife fuehrt."""

    def _text(self, mode):
        import sys
        sys.path.insert(0, str(WURZEL / "orchestrator"))
        from app.core.agent_manager import _render_claude_md
        return _render_claude_md([], mode=mode)

    def test_claude_gets_the_self_verification_cadence(self):
        """Anthropic: „Establish a method for checking your own work as you
        build; run it every [interval]"."""
        self.assertIn("pruefe dich selbst auf Takt", self._text("claude_code"))

    def test_codex_gets_the_narrow_the_request_stance(self):
        """OpenAI faengt `max_turns` ab und bittet um Eingrenzung."""
        text = self._text("codex_cli")
        self.assertIn("Wenn die Aufgabe zu gross ist", text)
        self.assertIn("wie man die Aufgabe teilt", text)

    def test_custom_llm_is_told_about_its_budget_notices(self):
        """Sonst kaemen die Systemmeldungen unseres Harness aus dem Nichts."""
        self.assertIn("Arbeitsbudget", self._text("custom_llm"))

    def test_an_unknown_mode_still_gets_something(self):
        self.assertTrue(self._text(None).strip())

    def test_the_notes_do_not_bleed_into_each_other(self):
        """Ein Codex-Agent soll nicht ueber unsere Budget-Systemmeldungen lesen,
        die er nie bekommt."""
        self.assertNotIn("Arbeitsbudget", self._text("codex_cli"))
        self.assertNotIn("Wenn die Aufgabe zu gross ist", self._text("custom_llm"))

    def test_every_write_path_passes_the_mode(self):
        """Vier Stellen schreiben die Anleitung. Eine ohne Modus bekaeme den
        falschen Abschnitt — genau die Sorte Luecke, die hier schon zweimal
        aufgetreten ist."""
        stellen = MANAGER.split("_render_claude_md(")[1:]
        for i, block in enumerate(stellen[1:], start=1):
            with self.subTest(aufruf=i):
                self.assertIn("mode=", block[:400])


if __name__ == "__main__":
    unittest.main()
