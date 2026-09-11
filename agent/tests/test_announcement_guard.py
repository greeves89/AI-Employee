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
import unittest.mock

from app.announcement_guard import NUDGE, promises_but_does_nothing
from app.providers.base import LLMEvent


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


class _Publisher:
    async def publish_chat(self, message_id, kind, payload):
        pass


class _Anbieter:
    """Antwortet je Zug mit einem festen Text und meldet den Zug als fertig.

    Ohne Werkzeugaufruf — genau die Lage aus dem Bericht: der Agent SAGT etwas
    und tut nichts.
    """

    # Der Anstupser-Zweig hebt sein eigenes Zugbudget an (max_turns =
    # num_turns + 4). Bliebe ``ansporn_offen`` stehen, fuettert sich die
    # Schleife selbst: Zusage -> Anstupser -> Budget -> Zusage. Gemessen am
    # 11.09.2026 waren das 5,4 GB in 2,5 Minuten. Ohne diese Bremse HAENGT
    # der Test dann, statt rot zu werden — und eine Gegenprobe, die haengt,
    # belegt nichts.
    MAX_AUFRUFE = 12

    def __init__(self, *texte):
        self.texte = list(texte)
        self.aufrufe = 0
        self.reasoning_effort = ""

    def stream_completion(self, messages, tools=None):
        if self.aufrufe >= self.MAX_AUFRUFE:
            raise AssertionError(
                f"Zug lief ueber {self.MAX_AUFRUFE} Anbieter-Aufrufe hinaus — "
                "die Schleife beendet sich nicht mehr selbst."
            )
        text = self.texte[min(self.aufrufe, len(self.texte) - 1)]
        self.aufrufe += 1

        async def gen():
            yield LLMEvent(type="text_delta", text=text)
            yield LLMEvent(type="done")

        return gen()

    async def close(self):
        pass


class ItIsWiredIntoTheChatTurnTests(unittest.IsolatedAsyncioTestCase):
    """Eine Pruefung, die niemand aufruft, aendert nichts.

    Vorher stand das hier als Textsuche im Quelltext des Chat-Handlers, zuletzt
    in einem 400-Zeichen-Fenster mit 111 Zeichen Luft. Ein Fenster misst
    Abstand, gemeint war Wirkung — siehe Issue #726. Jetzt laeuft der Zug.
    """

    ZUSAGE = ("Alles klar, ich kümmere mich sofort um die Taschenrechner-App. "
              "Ich erstelle sie komplett und deploye sie für dich.")

    def _handler(self):
        from app.llm_chat_handler import LLMChatHandler
        from app.providers.base import ChatMessage

        h = LLMChatHandler(log_publisher=_Publisher())
        h._context_window = 1_000_000
        # Vorbelegt, damit kein Systemprompt gebaut wird — der liest /workspace
        # und ist hier nicht die Frage.
        h._history = [ChatMessage(role="system", content="S")]
        return h

    async def _fahre(self, anbieter, *, budget=None):
        from app.config import settings

        h = self._handler()
        with unittest.mock.patch.object(h, "_get_provider", return_value=anbieter), \
             unittest.mock.patch.object(
                 h, "_get_tools", new=unittest.mock.AsyncMock(return_value=None)), \
             unittest.mock.patch.object(settings, "max_turns", budget or 20):
            ergebnis = await h.handle_message("m1", "bau mir eine App")
        return h, ergebnis

    def _anstupser(self, handler) -> int:
        return [m.content for m in handler._history].count(NUDGE)

    async def test_an_empty_promise_is_nudged(self):
        h, _ = await self._fahre(_Anbieter(self.ZUSAGE, "Ist erledigt."))

        self.assertEqual(self._anstupser(h), 1)

    async def test_the_agent_gets_a_turn_to_follow_it(self):
        """Ein Anstupser, nach dem der Zug endet, erreicht niemanden."""
        _, ergebnis = await self._fahre(_Anbieter(self.ZUSAGE, "Ist erledigt."))

        self.assertGreaterEqual(ergebnis["num_turns"], 2)

    async def test_ordinary_talk_is_left_alone(self):
        """Gegenstueck: im Chat ist Reden der Normalfall. Wuerde jeder
        werkzeuglose Zug angestupst, waere der Anstupser wertlos."""
        h, ergebnis = await self._fahre(_Anbieter("Hallo! Wie kann ich helfen?"))

        self.assertEqual(self._anstupser(h), 0)
        self.assertEqual(ergebnis["num_turns"], 1)

    async def test_only_once_per_human_message(self):
        """Ein zweiter Anstupser waere Bevormundung, wenn der Agent begruendet
        ablehnt."""
        h, _ = await self._fahre(_Anbieter(self.ZUSAGE, self.ZUSAGE, self.ZUSAGE))

        self.assertEqual(self._anstupser(h), 1)

    async def test_the_turn_budget_is_extended(self):
        """Ohne zusaetzliche Zuege koennte der Agent den Anstupser gar nicht
        mehr befolgen: das Budget ist in dem Moment schon aufgebraucht."""
        _, ergebnis = await self._fahre(
            _Anbieter(self.ZUSAGE, "Ist erledigt."), budget=1)

        self.assertGreaterEqual(ergebnis["num_turns"], 2)


if __name__ == "__main__":
    unittest.main()
