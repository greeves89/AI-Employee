"""Die Sprachfront muss die MCP-Server des Agenten SELBST benutzen.

Nutzerbericht vom 18.08.2026, mit Bildschirmfotos: unter Einstellungen →
Integrationen war ein MCP-Server mit 32 Werkzeugen angehakt. Auf die Frage
„siehst du die mcp tools" zaehlte die Stimme nur ihre eingebauten auf
(``get_agent_status``, ``list_agent_tasks``, …) — kein einziges davon. Sie
reichte den Auftrag per ``ask_agent`` weiter, und der Nutzer musste ihr am Ende
selbst sagen, dass es das Werkzeug gibt:

    „ich habe unter integration nun den MCP erstellt... und dem Bot zugewiesen!
     der soll diese Tools nutzen, stattdessen übergibt der voice layer IMMER an
     den agent!"

Ursache: die Werkzeugliste der Sprachfront stand vollstaendig von Hand im
Quelltext (47 Konstanten) und holte nirgends ein ``tools/list``. Sie KONNTE
nichts von den angebundenen Servern wissen.

Ausdrueckliche Vorgabe dazu: „ALLES was unter MCP Tools drin ist MUSS der agent
voice auch kennen" — deshalb pruefen diese Tests auch, dass nichts
stillschweigend gekuerzt wird.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.agent_mcp_servers import (
    WERKZEUG_BUDGET,
    MCPZiel,
    call_agent_tool,
    suche_im_katalog,
    voice_toolspecs,
)


def _server(name, tools, id_=1):
    return SimpleNamespace(
        id=id_, name=name, url=f"https://{name}.example/mcp", tools=tools,
        auth_token_encrypted=None, headers_encrypted=None, allow_private_host=False,
    )


def _werkzeug(name, beschreibung="tut etwas"):
    return {"name": name, "description": beschreibung,
            "inputSchema": {"type": "object", "properties": {}}}


class EveryToolReachesTheVoiceTests(unittest.TestCase):
    def test_all_tools_of_a_server_become_voice_tools(self):
        """Der gemeldete Server meldete 32 — es duerfen nicht 31 ankommen."""
        server = _server("Planer", [_werkzeug(f"tool_{i}") for i in range(32)])
        werkzeuge, plan, katalog = voice_toolspecs([server])
        self.assertEqual(len(werkzeuge), 32)
        self.assertEqual(len(plan), 32)

    def test_nothing_is_lost_when_the_budget_is_exceeded(self):
        """Die Engine macht bei etwa 128 Werkzeugen dicht — deklariert wird
        also nur, was passt. Erreichbar bleibt trotzdem ALLES, ueber
        mcp_search_tools + mcp_call_tool. Eine stille Kuerzung waere dasselbe
        Verhalten wie der gemeldete Fehler, nur an anderer Stelle."""
        server = _server("Viele", [_werkzeug(f"t{i}") for i in range(300)])
        werkzeuge, plan, katalog = voice_toolspecs([server], budget=79)
        self.assertEqual(len(werkzeuge), 79, "mehr deklariert als das Budget erlaubt")
        self.assertEqual(len(plan), 300, "nicht mehr alle aufrufbar")
        self.assertEqual(len(katalog), 300, "nicht mehr alle auffindbar")

    def test_the_budget_stays_under_the_engine_limit(self):
        self.assertLessEqual(WERKZEUG_BUDGET, 128)

    def test_several_servers_are_all_included(self):
        werkzeuge, _, _ = voice_toolspecs([
            _server("Test", [_werkzeug("a"), _werkzeug("b")], id_=4),
            _server("Planer", [_werkzeug("c")], id_=6),
        ])
        self.assertEqual(len(werkzeuge), 3)

    def test_the_service_name_is_visible_to_the_model(self):
        """Sonst kann es nicht sagen, WOHER eine Antwort kommt."""
        werkzeuge, _, _ = voice_toolspecs([_server("Planer", [_werkzeug("list_projects")])])
        self.assertIn("[Planer]", werkzeuge[0]["toolSpec"]["description"])

    def test_the_tool_keeps_its_own_name(self):
        """Der Nutzer sagt „list projects" — nicht „planer_list_projects"."""
        _, plan, _ = voice_toolspecs([_server("Planer", [_werkzeug("list_projects")])])
        self.assertIn("list_projects", plan)

    def test_junk_entries_do_not_take_the_rest_down(self):
        server = _server("Planer", [None, {"description": "ohne Namen"}, _werkzeug("gut")])
        werkzeuge, plan, katalog = voice_toolspecs([server])
        self.assertEqual(len(werkzeuge), 1)
        self.assertIn("gut", plan)


class TwoServersWithTheSameToolNameTests(unittest.TestCase):
    def test_the_second_one_is_prefixed_so_both_stay_reachable(self):
        """Ohne das hoerte das Modell einen Namen und wir riefen den falschen
        Server — oder eines der beiden Werkzeuge fiele weg."""
        werkzeuge, plan, katalog = voice_toolspecs([
            _server("Erster", [_werkzeug("search")], id_=1),
            _server("Zweiter", [_werkzeug("search")], id_=2),
        ])
        self.assertEqual(len(werkzeuge), 2)
        self.assertIn("search", plan)
        self.assertIn("zweiter_search", plan)
        self.assertEqual(plan["search"][0].name, "Erster")
        self.assertEqual(plan["zweiter_search"][0].name, "Zweiter")


class TheCallGoesToTheRightServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_calls_the_servers_url_with_the_original_name(self):
        _, plan, _ = voice_toolspecs([_server("Planer", [_werkzeug("list_projects")])])
        ziel, original = plan["list_projects"]
        antwort = {"result": {"content": [{"type": "text", "text": "4 Projekte"}]}}
        with patch("app.api.mcp_servers._call_tool", new=AsyncMock(return_value=antwort)) as ruf:
            text = await call_agent_tool(ziel, original, {"q": "x"})
        self.assertEqual(text, "4 Projekte")
        self.assertEqual(ruf.await_args.args[0], "https://Planer.example/mcp")
        self.assertEqual(ruf.await_args.args[1], "list_projects")

    async def test_an_error_is_put_into_words(self):
        """Still auf `ask_agent` auszuweichen war genau die Beschwerde — der
        Nutzer soll HOEREN, dass der Dienst nicht antwortet."""
        ziel = MCPZiel("Planer", "https://x.example/mcp", None, {}, False)
        antwort = {"error": {"message": "Server down"}}
        with patch("app.api.mcp_servers._call_tool", new=AsyncMock(return_value=antwort)):
            text = await call_agent_tool(ziel, "list_projects", {})
        self.assertIn("Server down", text)

    async def test_a_private_host_flag_is_carried_through(self):
        """Ein selbst gehosteter Server im Haus muss weiterhin erreichbar sein —
        aber nur, wenn ein Administrator ihn dafuer freigegeben hat."""
        ziel = MCPZiel("Intern", "https://mcp.intern.example/mcp", None, {}, True)
        with patch("app.api.mcp_servers._call_tool", new=AsyncMock(return_value={"result": {}})) as ruf:
            await call_agent_tool(ziel, "t", {})
        self.assertTrue(ruf.await_args.kwargs["allow_private"])


class TheVoiceSessionIsWiredToItTests(unittest.TestCase):
    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[1] / "app/services/realtime_voice_session.py").read_text()

    def test_the_session_loads_the_agents_servers(self):
        self.assertIn("servers_for_agent", self.QUELLE)
        self.assertIn("voice_toolspecs", self.QUELLE)

    def test_a_call_goes_straight_to_the_server_not_through_the_agent(self):
        block = self.QUELLE.split("if name in self._mcp_plan:", 1)
        self.assertEqual(len(block), 2, "keine Zustellung an die MCP-Server")
        self.assertIn("call_agent_tool", block[1][:700])

    def test_the_dispatch_runs_before_the_built_in_tools(self):
        """Sonst gewinnt ein gleichnamiges eingebautes Werkzeug."""
        self.assertLess(
            self.QUELLE.index("if name in self._mcp_plan:"),
            self.QUELLE.index('if name == "get_agent_status":'),
        )

    def test_the_model_is_told_the_tools_are_its_own(self):
        """Ohne diesen Satz reichte die Stimme selbst dann weiter, wenn sie das
        Werkzeug hatte."""
        self.assertIn("ANGEBUNDENE DIENSTE", self.QUELLE)
        self.assertIn("_mcp_note", self.QUELLE)

    def test_a_failure_to_load_does_not_kill_the_call(self):
        """Lieber ohne Fremdwerkzeuge reden als gar nicht."""
        block = self.QUELLE.split("MCP-Werkzeuge nicht ladbar", 1)
        self.assertEqual(len(block), 2)


if __name__ == "__main__":
    unittest.main()


class FindingAToolThatIsNotDeclaredTests(unittest.TestCase):
    """Was nicht ins Budget passt, muss auffindbar bleiben — sonst waere die
    Grenze der Engine eine stille Kuerzung."""

    def setUp(self):
        _, self.plan, self.katalog = voice_toolspecs(
            [_server("Planer", [
                _werkzeug("list_projects", "Listet alle Projekte auf"),
                _werkzeug("create_task", "Legt eine Aufgabe an"),
            ])],
            budget=0,  # nichts wird deklariert
        )

    def test_nothing_is_declared_but_everything_is_reachable(self):
        self.assertEqual(len(self.plan), 2)

    def test_searching_finds_the_tool_by_word(self):
        self.assertIn("list_projects", suche_im_katalog(self.katalog, "Projekte"))

    def test_searching_says_so_when_nothing_matches(self):
        self.assertIn("Kein Werkzeug gefunden", suche_im_katalog(self.katalog, "Zahnarzt"))

    def test_an_empty_query_still_shows_what_there_is(self):
        """Sonst steht das Modell vor einer leeren Antwort und behauptet wieder,
        es haette keinen Zugriff."""
        self.assertIn("list_projects", suche_im_katalog(self.katalog, ""))


class TheMetaToolsAreWiredTests(unittest.TestCase):
    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[1] / "app/services/realtime_voice_session.py").read_text()

    def test_both_meta_tools_exist(self):
        self.assertIn("MCP_SEARCH_TOOLS_TOOL", self.QUELLE)
        self.assertIn("MCP_CALL_TOOL_TOOL", self.QUELLE)

    def test_they_are_only_offered_when_there_are_mcp_tools(self):
        """Ohne angebundenen Dienst waeren sie zwei Werkzeuge, die nichts tun."""
        self.assertIn("if self._mcp_plan:", self.QUELLE)

    def test_the_declared_set_respects_a_budget(self):
        self.assertIn("WERKZEUG_BUDGET - len(_tools) - 2", self.QUELLE)

    def test_calling_an_unknown_name_points_back_at_the_search(self):
        block = self.QUELLE.split("Es gibt kein Werkzeug namens", 1)
        self.assertEqual(len(block), 2)
        self.assertIn("mcp_search_tools", block[1][:200])
