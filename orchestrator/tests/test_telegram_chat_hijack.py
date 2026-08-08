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

    def test_the_message_is_still_delivered_via_the_chat(self):
        """Kein Bot heisst nicht: Meldung weg. Sie landet im Chat des Agenten."""
        block = AGENTS_API.split("if sent_to == 0:", 1)[1][:3500]
        self.assertIn('session_id="meldungen"', block)
        self.assertIn('"delivered_via": "chat"', block)

    def test_the_agent_is_told_not_to_treat_it_as_an_error(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn('delivered_via: "chat"', mgr)
        self.assertIn("Nobody borrows anybody else's", mgr)


class TeamLeadIsTheWayTests(unittest.TestCase):
    """Kein eigener Bot heisst nicht sprachlos: der Team-Lead gibt weiter.

    Nicht als heimliche Weiterleitung, sondern als Bitte — der Lead entscheidet und
    schreibt unter SEINEM Namen. Damit bleibt fuer den Leser immer erkennbar, wer da
    schreibt; genau das war beim geliehenen Bot nicht der Fall.
    """

    def test_the_answer_names_the_actual_lead(self):
        block = AGENTS_API.split("if sent_to == 0:", 1)[1][:3500]
        self.assertIn("team_lead_for(_db, agent_id)", block)
        self.assertIn("send_message", block)

    def test_without_a_team_it_says_what_the_channel_is(self):
        block = AGENTS_API.split("if sent_to == 0:", 1)[1][:3500]
        self.assertIn("keinen Team-Lead", block)
        self.assertIn("mehr Kanaele", block)

    def test_a_lead_without_telegram_ends_the_chain_honestly(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("Have you no Telegram either?", mgr)
        self.assertIn("it stays in the chat", mgr)

    def test_the_member_is_told_to_ask_the_lead(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("ask your team lead to pass it on", mgr)
        self.assertIn("list_my_team", mgr)

    def test_the_lead_knows_it_is_a_filter_not_a_relay(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        self.assertIn("If you ARE a team lead", mgr)
        self.assertIn("you are the filter", mgr)

    def test_both_tools_exist_in_every_harness(self):
        """Harness-Paritaet: ohne `send_message`/`list_my_team` waere die Anweisung leer."""
        from pathlib import Path
        repo = Path(__file__).resolve().parents[2]
        mcp = (repo / "agent/mcp/orchestrator-server.mjs").read_text()
        defs = (repo / "agent/app/tools/definitions.py").read_text()
        self.assertIn('name: "send_message"', mcp)
        self.assertIn('name: "list_my_team"', mcp)
        self.assertIn('"name": "send_message"', defs)
        self.assertIn('"name": "list_team"', defs)


class SystemPromptParityTests(unittest.TestCase):
    """Die Regel muss im SYSTEMPROMPT stehen — und der ist fuer alle drei Laufzeiten
    dieselbe Vorlage (CLAUDE.md / AGENTS.md / AGENT.md aus `DEFAULT_CLAUDE_MD`).

    Im Proaktiv-Prompt allein stuende sie nur fuer geplante Laeufe; im Chat und in jeder
    normalen Aufgabe wuesste der Agent nichts davon und liehe sich wieder einen Bot.
    """

    @classmethod
    def setUpClass(cls):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        cls.template = mgr.split("DEFAULT_CLAUDE_MD = ", 1)[1].split("PROACTIVE_PROMPT", 1)[0]

    def test_the_rule_is_in_the_shared_system_prompt(self):
        self.assertIn("Telegram: nur mit EIGENEM Bot", self.template)
        self.assertIn("leihst du dir NIE", self.template)

    def test_it_names_the_way_out(self):
        self.assertIn("list_my_team", self.template)
        self.assertIn("send_message", self.template)

    def test_the_lead_side_is_covered_too(self):
        self.assertIn("Du bist der Filter", self.template)
        self.assertIn("Hast du selbst kein Telegram", self.template)

    def test_the_template_reaches_all_three_runtimes(self):
        """instructions_paths deckt Claude, Codex und Custom-LLM ab — sonst haette eine
        Laufzeit die Regel nicht."""
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        paths = mgr.split("def instructions_paths(", 1)[1].split("def _render_claude_md", 1)[0]
        self.assertIn("/workspace/CLAUDE.md", paths)
        self.assertIn("/workspace/AGENTS.md", paths)
        self.assertIn("/workspace/AGENT.md", paths)
