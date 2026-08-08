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
    def test_the_fallback_no_longer_sets_the_switch(self):
        fallback = AGENTS_API.split("Fallback for agents without their own Telegram bot", 1)[1][:2500]
        self.assertNotIn('setex(f"telegram:chat:{cid}:active_agent"', fallback)

    def test_the_reader_is_told_how_to_reach_the_other_agent(self):
        self.assertIn("/agent {agent_id}", AGENTS_API)

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
