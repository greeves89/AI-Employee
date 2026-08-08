"""Eine geliehene Meldung darf den Chat nicht kapern.

Ein Agent ohne eigenen Telegram-Bot leiht sich den eines anderen, um eine Meldung
loszuwerden. Dabei wurde die Weiche `telegram:chat:<id>:active_agent` fuer 24 Stunden
auf den LEIHENDEN Agenten gestellt — danach ging jede Nachricht des Nutzers an ihn,
und der Agent, dem der Bot gehoert, hoerte nie wieder etwas.

Genau so ist JujaBot verstummt: eine Meldung von CodeReview lief ueber JujaBots Bot,
seitdem stand im Log `gateway=9349c967 target=6e4210c1`. Der Nutzer schrieb an JujaBot
und bekam nichts zurueck.
"""

import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
AGENTS_API = (ORCH / "app/api/agents.py").read_text()
BOT = (ORCH / "app/telegram/agent_bot.py").read_text()


class BroadcastDoesNotHijackTests(unittest.TestCase):
    def test_nothing_sets_the_switch_behind_the_users_back(self):
        """Die Weiche darf NUR die ausdrueckliche Wahl des Nutzers setzen."""
        self.assertNotIn('setex(f"telegram:chat:{cid}:active_agent"', AGENTS_API)

    def test_the_explicit_choice_still_persists(self):
        """`/agent <Name>` ist die Wahl des NUTZERS — die bleibt."""
        self.assertIn('setex(f"telegram:chat:{chat_id}:active_agent", 86400, selected.id)', BOT)


class DeadSwitchTests(unittest.TestCase):
    def test_a_switch_to_a_deleted_agent_falls_back_to_the_owner(self):
        target = BOT.split("async def _active_target_agent_id", 1)[1].split("\n    async def ", 1)[0]
        self.assertIn("select(Agent.id).where(Agent.id == target)", target)
        self.assertIn('redis.delete(f"telegram:chat:{chat_id}:active_agent")', target)
        self.assertIn("return self.agent_id", target)


if __name__ == "__main__":
    unittest.main()


class NoBorrowingAtAllTests(unittest.TestCase):
    """Ein Agent ohne eigenen Token hat Pech — er kapert keinen fremden.

    Vorher lieferte ein Agent ohne Bot ueber den Bot eines anderen aus. Der Empfaenger
    konnte nicht erkennen, mit wem er eigentlich schreibt, und der Besitzer des Bots bekam
    Meldungen, die ihn nichts angingen. Genau so landeten die Arbeitsberichte von
    CodeReview — fuer den nie ein Telegram eingerichtet wurde — im JujaBot-Chat.
    """

    def test_the_fallback_is_gone(self):
        self.assertNotIn("Fallback for agents without their own Telegram bot", AGENTS_API)
        self.assertNotIn("fallback_bot", AGENTS_API)

    def test_the_answer_says_what_to_do_instead(self):
        self.assertIn("hat keinen eigenen Telegram-Bot", AGENTS_API)
        self.assertIn("eigenen Bot-Token", AGENTS_API)

    def test_the_agent_is_told_not_to_treat_it_as_an_error(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("A 503 here means YOU have no Telegram bot of your own", mgr)
        self.assertIn("nobody borrows anybody else's", mgr)
