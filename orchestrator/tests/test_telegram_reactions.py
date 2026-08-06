"""Reaktionen entscheidet der Agent — es gibt keine Automatik.

Kundenwunsch 2026-08-06: „der bot soll ja entscheiden welche Interaktion mit dem
chat er macht … like oder was auch immer … oder auch gar nichts."

Der erste Entwurf hatte feste Regeln (Augen bei jedem Eingang, Daumen bei jedem
Ende). Das wirkte mechanisch — und ausserdem hat genau diese Buchführung im
Eingangs-Handler die Zustellung zerlegt. Jetzt: ein Werkzeug, das der Agent nur
aufruft, wenn es passt. Der Normalfall ist KEINE Reaktion.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT = ROOT / "orchestrator/app/telegram/agent_bot.py"
API = ROOT / "orchestrator/app/api/telegram_actions.py"
PROMPT = ROOT / "agent/app/chat_consumer.py"


class NoAutomaticReactionsTests(unittest.TestCase):
    def test_bot_never_reacts_on_its_own(self):
        """Kein fest verdrahtetes Zeichen mehr — weder beim Eingang noch am Ende."""
        src = BOT.read_text()
        self.assertNotIn("_react(chat_id", src)
        self.assertNotIn("THUMBS UP SIGN", src)
        self.assertNotIn("FACE SCREAMING IN FEAR", src)

    def test_delivery_stays_guarded(self):
        """Die Lehre aus dem Ausfall: Beiwerk darf die Zustellung nie verhindern."""
        block = re.search(r"# Merken, welche Nachricht.*?_start_listener", BOT.read_text(), re.S)
        self.assertIsNotNone(block)
        self.assertIn("try:", block.group(0))
        self.assertIn("except Exception", block.group(0))


class ReactionEndpointTests(unittest.TestCase):
    def test_endpoint_exists(self):
        self.assertIn('@router.post("/react")', API.read_text())

    def test_only_telegram_approved_emojis_pass(self):
        """Ein anderes Zeichen weist Telegram ab — das faellt sonst erst zur Laufzeit auf."""
        from app.api.telegram_actions import _ALLOWED_REACTIONS
        self.assertGreater(len(_ALLOWED_REACTIONS), 15)
        self.assertIn("\N{THUMBS UP SIGN}", _ALLOWED_REACTIONS)
        self.assertIn("\N{HEAVY BLACK HEART}", _ALLOWED_REACTIONS)
        self.assertIn("\N{FACE SCREAMING IN FEAR}", _ALLOWED_REACTIONS)
        self.assertNotIn("\N{SNOWMAN}", _ALLOWED_REACTIONS)

    def test_unknown_emoji_is_refused_with_help(self):
        src = API.read_text()
        self.assertIn("status_code=400", src)
        self.assertIn("Moeglich sind", src)

    def test_empty_emoji_removes_the_reaction(self):
        self.assertIn("if emoji else []", API.read_text())


class AgentInstructionTests(unittest.TestCase):
    def test_agent_is_told_about_it(self):
        src = PROMPT.read_text()
        self.assertIn("REACTIONS", src)
        self.assertIn("/react", src)

    def test_sparingly_is_spelled_out(self):
        """Ohne diese Ansage reagiert das Modell auf alles — genau das sollte weg."""
        src = PROMPT.read_text()
        self.assertIn("NORMAL case is NO reaction", src)
        self.assertIn("do not react to every message", src)

    def test_reaction_never_replaces_an_answer(self):
        self.assertIn("Never use a reaction INSTEAD of an answer", PROMPT.read_text())

    def test_message_id_is_available(self):
        """Ohne die ID kann der Agent nicht auf die richtige Nachricht reagieren."""
        src = PROMPT.read_text()
        self.assertIn('msg_ref = tg.get("message_id"', src)
        self.assertIn("{msg_ref}", src)


if __name__ == "__main__":
    unittest.main()
