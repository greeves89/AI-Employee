"""„Ich mache das jetzt" — und dann passiert nichts.

Nutzerbericht vom 2026-08-16, per Sprache aufgenommen und in der Datenbank
nachlesbar:

    Nutzer:  bau mir mal eine kleine taschenrechner app
    Agent:   Alles klar, ich kuemmere mich sofort … ich plane jetzt die
             Entwicklung und den Deployment-Prozess          [KEIN Werkzeugaufruf]
    Nutzer:  und bloedelst du
    Agent:   Nein, ich arbeite ernsthaft … ich erstelle und deploye sie jetzt
                                                            [KEIN Werkzeugaufruf]
    Nutzer:  Hast du die App gebaut!!!???
    Agent:   Nein — die Taschenrechner-App wurde noch nicht gebaut.

Im Auftrags-Pfad ist genau das seit v1.178.2 abgesichert
(``llm_runner._compliance_gaps``). Der Chat-Pfad hatte **null** Absicherung —
und die Sprachfront laeuft ueber den Chat.

Die Pruefung ist bewusst enger als beim Auftrag: im Chat ist Reden der
Normalfall („hallo", „erklaer mir X"). Ausloeser ist nicht die fehlende Arbeit,
sondern der **Widerspruch** zwischen Zusage und Untaetigkeit — der ist
nachweisbar falsch, egal worum es geht.
"""

import unittest

from app.announcement_guard import NUDGE, promises_but_does_nothing


class TheRealCaseIsCaughtTests(unittest.TestCase):
    """Woertlich die Saetze aus dem Gespraech."""

    def test_the_first_empty_promise(self):
        self.assertTrue(promises_but_does_nothing(
            "Alles klar, ich kümmere mich sofort um die Taschenrechner-App. "
            "Ich erstelle sie komplett und deploye sie für dich.", set()))

    def test_the_second_empty_promise(self):
        self.assertTrue(promises_but_does_nothing(
            "Nein, ich blödel nicht — ich arbeite ernsthaft an deiner "
            "Taschenrechner-App. Ich erstelle und deploye sie jetzt für dich.", set()))

    def test_english_works_too(self):
        for text in ("I'll now build the calculator app for you.",
                     "I'm going to create the app right away.",
                     "Let me build that now."):
            with self.subTest(text=text):
                self.assertTrue(promises_but_does_nothing(text, set()))


class OrdinaryTalkIsLeftAloneTests(unittest.TestCase):
    """Ein Anstupser bei jedem werkzeuglosen Zug waere teuer und laestig — im
    Chat ist Reden meistens genau richtig."""

    def test_a_greeting(self):
        self.assertFalse(promises_but_does_nothing("Hallo! Wie kann ich helfen?", set()))

    def test_an_explanation(self):
        self.assertFalse(promises_but_does_nothing(
            "Ein Taschenrechner besteht aus einer Anzeige und den Tasten. "
            "Man kann ihn mit HTML und JavaScript bauen.", set()))

    def test_a_question_back(self):
        self.assertFalse(promises_but_does_nothing(
            "Soll die App auch wissenschaftliche Funktionen haben?", set()))

    def test_a_refusal_with_a_reason(self):
        """Wer begruendet ablehnt, hat nichts zugesagt — und soll nicht
        angestupst werden."""
        self.assertFalse(promises_but_does_nothing(
            "Das kann ich nicht bauen, mir fehlen die Schreibrechte im "
            "Arbeitsverzeichnis.", set()))

    def test_reporting_finished_work(self):
        """Vergangenheit ist keine Zusage."""
        self.assertFalse(promises_but_does_nothing(
            "Ich habe die App gebaut und deployed.", set()))

    def test_empty_text(self):
        self.assertFalse(promises_but_does_nothing("", set()))
        self.assertFalse(promises_but_does_nothing("   ", None))


class RealWorkSilencesItTests(unittest.TestCase):
    def test_a_promise_with_actual_work_is_fine(self):
        """Wer ankuendigt UND anfaengt, macht alles richtig."""
        self.assertFalse(promises_but_does_nothing(
            "Ich erstelle die App jetzt.", {"write_file", "bash"}))

    def test_only_looking_around_does_not_count(self):
        """Genau die Falle aus dem Bericht: drei Blicke in die eigene
        Wissensdatei sehen nach Arbeit aus und sind keine."""
        self.assertTrue(promises_but_does_nothing(
            "Ich erstelle die App jetzt.", {"search_memory", "brain_search"}))

    def test_creating_a_task_counts_as_work(self):
        """Delegieren IST handeln — der Agent muss es nicht selbst tun."""
        self.assertFalse(promises_but_does_nothing(
            "Ich kümmere mich jetzt darum.", {"create_task"}))


class TheNudgeItselfTests(unittest.TestCase):
    def test_it_names_both_ways_forward(self):
        """Selbst machen oder delegieren — sonst antwortet der Agent mit einer
        weiteren Ankuendigung."""
        self.assertIn("create_task", NUDGE)
        self.assertIn("selbst", NUDGE)

    def test_it_allows_an_honest_no(self):
        """Ohne diesen Ausweg erfindet ein Agent, der es nicht kann, Arbeit."""
        self.assertIn("sag WARUM", NUDGE)

    def test_it_is_not_a_reprimand(self):
        """Er soll anfangen, nicht sich rechtfertigen."""
        for wort in ("Fehler", "falsch", "gelogen", "entschuldige"):
            with self.subTest(wort=wort):
                self.assertNotIn(wort, NUDGE)


class ItIsWiredIntoTheChatTurnTests(unittest.TestCase):
    """Eine Pruefung, die niemand aufruft, aendert nichts."""

    import pathlib

    SRC = (pathlib.Path(__file__).resolve().parents[1]
           / "app/llm_chat_handler.py").read_text()

    def test_the_chat_handler_uses_it(self):
        self.assertIn("announcement_guard.promises_but_does_nothing(", self.SRC)

    def test_it_fires_before_the_turn_ends(self):
        """Nach ``break`` waere es wirkungslos."""
        block = self.SRC.split("if not has_tool_calls:", 1)[1].split("break", 1)[0]
        self.assertIn("promises_but_does_nothing", block)

    def test_only_once_per_human_message(self):
        """Ein zweiter Anstupser waere Bevormundung, wenn der Agent begruendet
        ablehnt."""
        self.assertIn("ansporn_offen = True", self.SRC)
        self.assertIn("ansporn_offen = False", self.SRC)

    def test_the_turn_budget_is_extended(self):
        """Ohne zusaetzliche Zuege koennte der Agent den Anstupser gar nicht
        mehr befolgen."""
        block = self.SRC.split("ansporn_offen = False", 1)[1][:400]
        self.assertIn("max_turns = num_turns + 4", block)


if __name__ == "__main__":
    unittest.main()
