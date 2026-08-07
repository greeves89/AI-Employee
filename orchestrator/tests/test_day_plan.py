"""Der sichtbare Tagesplan — und die Harness-Paritaet des Werkzeugs dazu.

Der Plan lag bisher nur in `/workspace/.agent_state.md` im Container: nicht in der
Datenbank, also nirgends anzeigbar und von niemandem korrigierbar. Jetzt schreibt der
Agent ihn ueber `plan_day` weg, der Kalender zeigt ihn, und ein gestrichener Block ist
fuer den naechsten Lauf vom Tisch.

Harte Vorgabe des Nutzers: bei den Harnessen muss ALLES gleich sein. Deshalb prueft der
zweite Block, dass `plan_day`/`get_day_plan` in JEDER Laufzeit existiert — MCP (Claude
Code), definitions.py + api_client.py (Codex/Custom-LLM) und im Sprachfront.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"
AGENT = REPO / "agent"


class ApiContractTests(unittest.TestCase):
    """Die Regeln, die den Plan brauchbar statt gefaehrlich machen."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ORCH / "app/api/day_plan.py").read_text()

    def test_replacing_a_plan_keeps_what_already_happened(self):
        """Neuplanung darf nur Geplantes/Gestrichenes ersetzen — niemals die Geschichte.

        Sonst loescht der 14-Uhr-Lauf, was der 7-Uhr-Lauf am Morgen erledigt hat. Die
        Regel liegt in ``core/day_plan_store``, damit API und Sprachweg dieselbe benutzen.
        """
        store = (ORCH / "app/core/day_plan_store.py").read_text()
        self.assertIn('AgentPlanItem.status.in_(("planned", "dropped"))', store)
        self.assertIn("from app.core.day_plan_store import replace_plan", self.src)

    def test_agent_can_only_touch_its_own_plan(self):
        self.assertIn("Agent can only touch its own day plan", self.src)
        self.assertIn("is_agent_principal", self.src)

    def test_user_access_is_checked_against_visible_agents(self):
        self.assertIn("visible_agent_ids", self.src)

    def test_status_values_are_validated(self):
        self.assertIn('VALID_STATUS = ("planned", "running", "done", "dropped")', self.src)
        self.assertIn("Unbekannter Status", self.src)

    def test_plan_size_is_capped(self):
        self.assertIn("MAX_PLAN_ITEMS", self.src)

    def test_router_is_actually_mounted(self):
        """Ein Endpunkt, den niemand einhaengt, ist kein Endpunkt."""
        router = (ORCH / "app/api/router.py").read_text()
        self.assertIn("day_plan", router)
        self.assertIn("api_router.include_router(day_plan.router)", router)

    def test_table_is_created_on_startup(self):
        """Das Projekt legt neue Tabellen per Startup-Ensure an (kein Alembic-Head-Konflikt)."""
        main = (ORCH / "app/main.py").read_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS agent_plan_items", main)
        self.assertIn("ix_plan_items_agent_date", main)


class HarnessParityTests(unittest.TestCase):
    """plan_day/get_day_plan muss in JEDER Laufzeit vorhanden sein."""

    def test_claude_code_via_mcp(self):
        mcp = (AGENT / "mcp/orchestrator-server.mjs").read_text()
        for tool in ("plan_day", "get_day_plan"):
            self.assertIn(f'name: "{tool}"', mcp, f"{tool} fehlt im MCP-Werkzeugkatalog")
            self.assertIn(f'case "{tool}":', mcp, f"{tool} hat keinen MCP-Handler")

    def test_codex_and_custom_llm_via_definitions_and_client(self):
        defs = (AGENT / "app/tools/definitions.py").read_text()
        client = (AGENT / "app/tools/api_client.py").read_text()
        for tool in ("plan_day", "get_day_plan"):
            self.assertIn(f'"name": "{tool}"', defs, f"{tool} fehlt in definitions.py")
            self.assertIn(f"async def {tool}(", client, f"{tool} fehlt im API-Client")

    def test_voice_front_can_read_the_plan(self):
        voice = (ORCH / "app/services/realtime_voice_session.py").read_text()
        self.assertIn('"name": "get_day_plan"', voice)
        self.assertIn('if name == "get_day_plan":', voice)
        self.assertIn("GET_DAY_PLAN_TOOL", voice)
        # ... und das Werkzeug muss auch in der Liste stehen, nicht nur definiert sein.
        tool_list = voice.split("_tools = [", 1)[1].split("]", 1)[0]
        self.assertIn("GET_DAY_PLAN_TOOL", tool_list)

    def test_custom_llm_has_it_without_searching_first(self):
        """Im Custom-LLM-Weg sind nur Kern-Werkzeuge direkt geladen — der Plan gehoert dazu."""
        chat = (AGENT / "app/llm_chat_handler.py").read_text()
        core = chat.split("CORE_TOOL_NAMES = {", 1)[1].split("}", 1)[0]
        self.assertIn('"plan_day"', core)
        self.assertIn('"get_day_plan"', core)

    def test_proactive_prompt_tells_every_agent_to_publish_its_plan(self):
        mgr = (ORCH / "app/core/agent_manager.py").read_text()
        step1 = mgr.split("## STEP 1: SURVEY AND PLAN THE RUN", 1)[1].split("## STEP 2", 1)[0]
        self.assertIn("plan_day", step1)
        self.assertIn("get_day_plan", step1)
        # Und die Regel, dass ein gestrichener Block nicht heimlich zurueckkehrt.
        self.assertIn("dropped", step1.lower())


class FrontendTests(unittest.TestCase):
    def test_calendar_shows_the_plan_and_lets_you_drop_a_block(self):
        tl = (REPO / "frontend/src/components/activity/activity-timeline.tsx").read_text()
        self.assertIn("getDayPlan", tl)
        self.assertIn("dropBlock", tl)
        # Bloecke ohne Uhrzeit duerfen nicht verschwinden.
        self.assertIn("undatedPlan", tl)


class VoiceTriggersTheAgentTests(unittest.TestCase):
    """Die Stimme plant NICHT selbst — sie gibt die Planung an den Agenten.

    Im Gespraech sagte der Agent „ich richte das jetzt ein" und es geschah nichts: der
    Sprachfront konnte den Plan lesen, aber weder schreiben noch die Arbeit abgeben.
    """

    @classmethod
    def setUpClass(cls):
        cls.voice = (ORCH / "app/services/realtime_voice_session.py").read_text()

    def test_tool_exists_and_is_offered(self):
        self.assertIn('"name": "plan_my_day"', self.voice)
        tool_list = self.voice.split("_tools = [", 1)[1].split("]", 1)[0]
        self.assertIn("PLAN_MY_DAY_TOOL", tool_list)

    def test_it_dispatches_a_real_task(self):
        handler = self.voice.split("async def _plan_my_day", 1)[1].split("async def _plan_task", 1)[0]
        self.assertIn("self._plan_task(", handler)
        # ... und schreibt NICHT selbst in die Plan-Tabelle.
        self.assertNotIn("AgentPlanItem", handler)

    def test_instruction_tells_the_agent_to_use_its_own_tools(self):
        handler = self.voice.split("async def _plan_my_day", 1)[1].split("async def _plan_task", 1)[0]
        for tool in ("get_day_plan", "list_todos", "plan_day"):
            self.assertIn(tool, handler)

    def test_prompt_forbids_announcing_without_doing(self):
        self.assertIn("NICHTS ANKÜNDIGEN, WAS DU NICHT IM SELBEN ZUG TUST", self.voice)


if __name__ == "__main__":
    unittest.main()
