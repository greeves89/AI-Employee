"""Ein Chat darf sich nicht selbst erwuergen.

Im Betrieb blieb ein Telegram-Chat dauerhaft mit „prompt too long" stehen: die
naechste Nachricht scheiterte identisch, und die uebernaechste auch. Erst ein
Zuruecksetzen von Hand half. Zwei Ursachen, beide hier festgenagelt:

1. Jede einzelne Nachricht schleppte die rund neunzig Zeilen lange
   Telegram-API-Referenz und die Autonomie-Regeln erneut mit — obwohl der Agent
   beides ab dem zweiten Zug im --resume-Verlauf hat. Der feste Aufschlag wuchs
   also mit jedem Zug mit, statt einmalig zu sein.
2. Kam der Laengenfehler dann, blieb der zu grosse Verlauf in der Sitzung stehen.
   Es gab keinen Zweig, der ihn erkannte — anders als beim laengst behandelten
   „no conversation found". Der Chat war tot.
"""

import unittest
from unittest.mock import patch


class TelegramPromptGrowthTests(unittest.TestCase):
    """Der feste Aufschlag gehoert an den Sitzungsanfang, nicht an jede Nachricht."""

    @staticmethod
    def _build(is_new_session):
        from app.chat_consumer import _build_telegram_prompt
        return _build_telegram_prompt(
            "Wie ist der Stand?",
            {"chat_id": "4711", "first_name": "Grevvy", "message_id": 9},
            is_new_session=is_new_session,
        )

    def test_the_first_message_still_carries_the_full_api_reference(self):
        """Einmal muss der Agent alles bekommen — sonst kann er keine Datei senden."""
        prompt = self._build(True)
        self.assertIn("send-document-upload", prompt)
        self.assertIn("send-voice-upload", prompt)
        self.assertIn("ORCHESTRATOR TELEGRAM API", prompt)

    def test_a_follow_up_message_does_not_repeat_it(self):
        prompt = self._build(False)
        self.assertNotIn("ORCHESTRATOR TELEGRAM API", prompt)
        self.assertNotIn("send-document-upload", prompt)

    def test_a_follow_up_is_dramatically_shorter(self):
        """Die Zahl ist der eigentliche Punkt: der Aufschlag pro Zug muss klein sein."""
        first = self._build(True)
        follow_up = self._build(False)
        self.assertLess(len(follow_up) * 3, len(first),
                        f"Folgezug {len(follow_up)} vs. erster Zug {len(first)} Zeichen")

    def test_the_follow_up_still_says_where_to_look_and_what_is_forbidden(self):
        """Kuerzen heisst nicht: den Agenten dumm zuruecklassen."""
        prompt = self._build(False)
        self.assertIn("api.telegram.org", prompt)
        self.assertIn("/api/v1/telegram", prompt)

    def test_the_users_message_survives_both_ways(self):
        for is_new in (True, False):
            with self.subTest(is_new=is_new):
                self.assertIn("Wie ist der Stand?", self._build(is_new))


class ApprovalRulesRepetitionTests(unittest.TestCase):
    """Die Autonomie-Regeln gehoeren einmal in die Sitzung — und wieder hinein,
    wenn der Nutzer sie aendert."""

    def _consumer(self):
        from app.chat_consumer import ChatConsumer
        return ChatConsumer.__new__(ChatConsumer)

    class _Handler:
        def __init__(self, session_id):
            self.session_id = session_id

    def _prepare(self, consumer, handler, rules):
        with patch("app.runner_hooks.get_approval_rules_prefix", lambda: rules), \
             patch("app.runner_hooks.get_skills_context", lambda: ""), \
             patch("app.runner_hooks.get_marketplace_skill_suggestions", lambda t: ""):
            return consumer._prepare_text("hallo", None, "webapp", handler)

    def test_the_rules_are_sent_when_the_session_starts(self):
        consumer = self._consumer()
        out = self._prepare(consumer, self._Handler(None), "=== REGELN ===")
        self.assertIn("=== REGELN ===", out)

    def test_unchanged_rules_are_not_repeated_on_every_turn(self):
        consumer = self._consumer()
        self._prepare(consumer, self._Handler(None), "=== REGELN ===")
        out = self._prepare(consumer, self._Handler("sess-1"), "=== REGELN ===")
        self.assertNotIn("=== REGELN ===", out)

    def test_changed_rules_reach_the_agent_again(self):
        """Sonst arbeitet er mit einer Freigabe weiter, die der Nutzer zurueckgezogen hat."""
        consumer = self._consumer()
        self._prepare(consumer, self._Handler(None), "=== ALT ===")
        out = self._prepare(consumer, self._Handler("sess-1"), "=== NEU ===")
        self.assertIn("=== NEU ===", out)


class ContextLengthRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Ein Laengenfehler muss die Sitzung neu starten, nicht den Chat toeten."""

    def _handler(self):
        from app.chat_handler import ChatHandler

        handler = ChatHandler.__new__(ChatHandler)
        handler.session_id = "sess-alt"
        handler.log_publisher = self._Publisher()
        return handler

    class _Publisher:
        def __init__(self):
            self.messages = []

        async def publish_chat(self, message_id, role, payload):
            self.messages.append(payload.get("message", ""))

    @staticmethod
    def _results(handler, sequence):
        """Laesst _execute_cli der Reihe nach die uebergebenen Antworten liefern."""
        calls = []

        async def fake_execute(message_id, text, model):
            calls.append(handler.session_id)
            return sequence[len(calls) - 1]

        handler._execute_cli = fake_execute
        return calls

    async def test_it_recognises_the_error_in_its_many_wordings(self):
        from app.chat_handler import _is_context_length_error

        for wording in (
            "API Error: prompt is too long: 215000 tokens > 200000 maximum",
            "Error: prompt too long",
            "context_length_exceeded",
            "This model's maximum context length is 200000 tokens",
        ):
            with self.subTest(wording):
                self.assertTrue(_is_context_length_error(wording))

    async def test_an_ordinary_error_is_not_mistaken_for_one(self):
        from app.chat_handler import _is_context_length_error

        self.assertFalse(_is_context_length_error("401 unauthorized"))
        self.assertFalse(_is_context_length_error(""))

    async def test_the_session_is_reset_and_the_message_answered(self):
        handler = self._handler()
        calls = self._results(handler, [
            {"status": "error", "error": "prompt is too long: 215000 tokens > 200000"},
            {"status": "success", "response": "Hier ist der Stand."},
        ])

        with patch("app.config.get_oauth_token", lambda: "tok"):
            result = await handler._run_turn_with_retries("m1", "Stand?", "sonnet")

        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, ["sess-alt", None],
                         "Der zweite Versuch muss OHNE die zu grosse Sitzung laufen")

    async def test_the_user_is_told_what_happened(self):
        """Ein stiller Gedaechtnisverlust mitten im Gespraech verwirrt mehr als er hilft."""
        handler = self._handler()
        self._results(handler, [
            {"status": "error", "error": "prompt too long"},
            {"status": "success", "response": "ok"},
        ])

        with patch("app.config.get_oauth_token", lambda: "tok"):
            await handler._run_turn_with_retries("m1", "Stand?", "sonnet")

        self.assertTrue(handler.log_publisher.messages)
        self.assertIn("zu lang", handler.log_publisher.messages[0].lower())

    async def test_a_length_error_does_not_wait_for_a_new_access_token(self):
        """„215000 tokens" enthaelt „token" — frueher lief der Zug damit in die
        Token-Erneuerung und wartete auf etwas, das nie kam."""
        handler = self._handler()
        self._results(handler, [
            {"status": "error", "error": "prompt is too long: 215000 tokens > 200000"},
            {"status": "error", "error": "prompt is too long: 215000 tokens > 200000"},
        ])
        waited = []

        async def fake_wait(before, **kwargs):
            waited.append(before)
            return "neu"

        with patch("app.config.get_oauth_token", lambda: "tok"), \
             patch("app.config.wait_for_new_oauth_token", fake_wait):
            await handler._run_turn_with_retries("m1", "Stand?", "sonnet")

        self.assertEqual(waited, [], "Ein Laengenfehler ist kein Zugangsfehler")

    async def test_a_fresh_session_is_not_retried_in_a_loop(self):
        """Ohne Verlauf ist die EINE Nachricht zu gross — ein zweiter Versuch
        scheitert identisch und kostet nur Zeit."""
        handler = self._handler()
        handler.session_id = None
        calls = self._results(handler, [
            {"status": "error", "error": "prompt too long"},
        ])

        with patch("app.config.get_oauth_token", lambda: "tok"):
            result = await handler._run_turn_with_retries("m1", "Riesentext", "sonnet")

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "error")

    async def test_an_auth_error_still_gets_its_retry(self):
        """Der bestehende Zugangs-Zweig darf durch die neue Pruefung nicht ausfallen."""
        handler = self._handler()
        calls = self._results(handler, [
            {"status": "error", "error": "401 access token has been revoked"},
            {"status": "success", "response": "ok"},
        ])

        async def fake_wait(before, **kwargs):
            return "neu"

        with patch("app.config.get_oauth_token", lambda: "alt"), \
             patch("app.config.wait_for_new_oauth_token", fake_wait):
            result = await handler._run_turn_with_retries("m1", "Stand?", "sonnet")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
