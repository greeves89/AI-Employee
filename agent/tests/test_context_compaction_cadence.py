"""Wie OFT verdichtet wird — nicht ob.

Der Anlass ist eine Kundenmeldung: „er komprimiert mir etwas zu oft". Dahinter
steckten drei getrennte Fehler, und jeder für sich hätte gereicht:

1. **Zwei Maßstäbe.** Ausgelöst wurde am echten Token-Zähler der Schnittstelle
   (System-Prompt + Werkzeug-Schemata + Verlauf), geprüft wurde danach an einer
   Zeichen-Schätzung, die nur den Verlauf kennt. Die Werkzeug-Schemata allein
   sind rund 16k Token, die die Prüfung nie sah — also meldete die Auslösung
   Not, wo die Prüfung keine fand, und das in jedem Zug aufs Neue.

2. **Keine Hysterese.** Verdichtet wurde nur bis knapp unter die Auslöseschwelle.
   Eine Werkzeugausgabe später war man wieder darüber.

3. **Die Summe als Größe.** Im Aufgabenlauf wurde die *aufaddierte* Eingabe aller
   Züge als aktuelle Kontextgröße gelesen. Die wächst zwangsläufig, auch wenn der
   Verlauf gleich bleibt — und der Reset danach warf die bis dahin gezählten
   Kosten weg.

Dazu kam das Sichtbare: der Hinweis „[Kontext wird komprimiert...]" wurde
gesendet, BEVOR feststand, ob es etwas zu tun gibt. Ein Lauf ohne Wirkung sah
für den Nutzer aus wie eine Verdichtung.
"""

import unittest

from app import context_compressor as cc


class ThresholdTests(unittest.TestCase):
    def test_the_target_is_clearly_below_the_trigger(self):
        """Sonst kippt die nächste Werkzeugausgabe den Kontext sofort zurück."""
        for window in (128_000, 200_000, 1_000_000):
            with self.subTest(window=window):
                trigger = cc.effective_threshold_tokens(window)
                target = cc.compaction_target_tokens(window)
                self.assertLess(target, trigger)
                # Nicht nur ein Hauch darunter — sonst ist es keine Hysterese.
                self.assertLessEqual(target, trigger * 0.8)

    def test_the_absolute_budget_still_caps_huge_windows(self):
        self.assertEqual(cc.effective_threshold_tokens(1_000_000), cc.ABSOLUTE_COMPACTION_BUDGET)
        # …und auf kleinen Fenstern gewinnt weiterhin der Anteil.
        self.assertEqual(cc.effective_threshold_tokens(8_192), 6_144)

    def test_a_fruitless_run_is_not_repeated_immediately(self):
        self.assertGreater(cc.reattempt_growth_tokens(1_000_000), 0)


class DeterministicLayerTests(unittest.TestCase):
    """Das Ziel ist ein absolutes Budget, kein Anteil am Modellfenster.

    Vorher war es ``0.55 * context_window``. Auf einem 1M-Modell waren das
    550.000 Token — weit ÜBER der Auslöseschwelle von 150.000. Die Kette brach
    deshalb immer schon nach der ersten Schicht ab; Microcompact und Collapse
    liefen auf genau den Modellen nie, auf denen der Kontext groß wird.
    """

    def _msg(self, role, content, tool_calls=None):
        from app.providers.base import ChatMessage

        m = ChatMessage(role=role, content=content)
        if tool_calls is not None:
            m.tool_calls = tool_calls
        return m

    def test_later_layers_run_when_the_budget_is_tight(self):
        msgs = [
            self._msg("system", "S"),
            self._msg("assistant", "Let me help you with that.\nEcht wichtig."),
            self._msg("tool", "x" * 8_000),
        ]
        _out, applied = cc.compress_messages(msgs, target_tokens=10)
        self.assertIn("snip", applied)
        self.assertIn("microcompact", applied)

    def test_it_stops_once_the_budget_is_met(self):
        """Ein großzügiges Ziel darf nicht unnötig am Text herumschneiden."""
        msgs = [
            self._msg("system", "S"),
            self._msg("assistant", "Let me help you with that.\nEcht wichtig."),
            self._msg("tool", "x" * 8_000),
        ]
        _out, applied = cc.compress_messages(msgs, target_tokens=1_000_000)
        self.assertEqual(applied, ["snip"])


class OverheadCalibrationTests(unittest.TestCase):
    """Ein Maßstab für Auslösung UND Kontrolle."""

    def _handler(self):
        from app.llm_chat_handler import LLMChatHandler

        return LLMChatHandler(log_publisher=None)

    def _fill(self, handler, n=10, size=2_000):
        from app.providers.base import ChatMessage

        handler._history = [ChatMessage(role="system", content="S" * size)]
        for i in range(n):
            handler._history.append(ChatMessage(role="user", content="u" * size))
            handler._history.append(ChatMessage(role="assistant", content=f"a{i}" * size))

    def test_the_estimate_learns_the_invisible_part_of_the_prompt(self):
        h = self._handler()
        self._fill(h)
        visible = cc.estimate_tokens(h._history)
        # Die Schnittstelle berechnet 40k mehr, als wir im Verlauf sehen können:
        # Werkzeug-Schemata plus Tokenizer-Abweichung.
        h._note_real_input_tokens(visible + 40_000)
        self.assertEqual(h._estimate_tokens(), visible + 40_000)

    def test_the_overhead_never_shrinks_back_to_optimism(self):
        """Ein Anbieter mit Prompt-Zwischenspeicher meldet mal weniger. Den
        kleineren Wert zu übernehmen hieße, den Kontext zu unterschätzen."""
        h = self._handler()
        self._fill(h)
        visible = cc.estimate_tokens(h._history)
        h._note_real_input_tokens(visible + 40_000)
        h._note_real_input_tokens(visible + 5_000)
        self.assertEqual(h._overhead_tokens, 40_000)

    def test_a_zero_report_is_not_a_measurement(self):
        h = self._handler()
        self._fill(h)
        h._note_real_input_tokens(0)
        self.assertEqual(h._overhead_tokens, 0)

    def test_trigger_and_check_agree(self):
        """Der Kern des Fehlers: die Auslösung sah 150k, die Kontrolle 110k —
        also wurde angekündigt und nichts getan, Zug für Zug."""
        h = self._handler()
        self._fill(h, n=40, size=4_000)
        window = 1_000_000
        h._context_window = window
        visible = cc.estimate_tokens(h._history)
        h._note_real_input_tokens(visible + 30_000)
        self.assertTrue(h._needs_compaction())
        # Genau der Wert, an dem ausgelöst wurde, ist auch der, den die Kontrolle sieht.
        self.assertEqual(h._estimate_tokens(), visible + 30_000)

    def test_a_fruitless_run_is_not_retried_every_turn(self):
        h = self._handler()
        h._context_window = 1_000_000
        self._fill(h, n=40, size=4_000)
        h._note_real_input_tokens(cc.estimate_tokens(h._history) + 30_000)
        self.assertTrue(h._needs_compaction())
        # Der Lauf konnte das Ziel nicht erreichen und merkt sich das.
        h._compaction_floor = h._estimate_tokens()
        self.assertFalse(h._needs_compaction())

    def test_but_real_growth_gets_another_attempt(self):
        from app.providers.base import ChatMessage

        h = self._handler()
        h._context_window = 1_000_000
        self._fill(h, n=40, size=4_000)
        h._note_real_input_tokens(cc.estimate_tokens(h._history) + 30_000)
        h._compaction_floor = h._estimate_tokens()
        growth = cc.reattempt_growth_tokens(1_000_000)
        h._history.append(ChatMessage(role="tool", content="x" * (growth * 4 + 100)))
        self.assertTrue(h._needs_compaction())

    def test_a_short_conversation_is_never_compacted(self):
        h = self._handler()
        self._fill(h, n=1, size=200_000)
        h._note_real_input_tokens(900_000)
        self.assertFalse(h._needs_compaction())


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    """Gesagt wird, was passiert ist — nicht, was vielleicht passieren wird."""

    class _Publisher:
        def __init__(self):
            self.texts = []

        async def publish_chat(self, message_id, kind, payload):
            if kind == "text":
                self.texts.append(payload.get("text", ""))

    def _handler(self, publisher):
        from app.llm_chat_handler import LLMChatHandler

        h = LLMChatHandler(log_publisher=publisher)
        h._context_window = 1_000_000
        return h

    async def test_a_run_without_effect_stays_silent(self):
        from app.providers.base import ChatMessage

        pub = self._Publisher()
        h = self._handler(pub)
        # Kurzer Verlauf, aber gewaltiger Aufschlag: nichts zu schneiden, nichts
        # alt genug zum Zusammenfassen — die Lage, in der frueher der Hinweis
        # ohne jede Wirkung erschien.
        h._history = [ChatMessage(role="system", content="S" * 400)]
        for i in range(6):
            h._history.append(ChatMessage(role="user", content=f"kurz {i}"))
        h._overhead_tokens = 200_000
        await h._compact_history("m1")
        self.assertEqual(pub.texts, [])
        # …und der naechste Zug wiederholt es nicht.
        self.assertTrue(h._compaction_floor)
        self.assertFalse(h._needs_compaction())

    async def test_a_real_reduction_is_reported_with_numbers(self):
        from app.providers.base import ChatMessage

        pub = self._Publisher()
        h = self._handler(pub)
        h._history = [ChatMessage(role="system", content="S" * 400)]
        for i in range(8):
            h._history.append(ChatMessage(role="assistant", content=f"a{i}"))
            h._history.append(ChatMessage(role="tool", content="x" * 200_000))
        before = h._estimate_tokens()
        await h._compact_history("m1")
        self.assertEqual(len(pub.texts), 1)
        self.assertIn("verdichtet", pub.texts[0])
        self.assertLess(h._estimate_tokens(), before)


if __name__ == "__main__":
    unittest.main()
