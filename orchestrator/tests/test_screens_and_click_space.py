"""Mehrere Bildschirme — und das Modell weiss, wie gross das Bild ist.

Nutzerwunsch vom 21.08.2026, woertlich: „WEITERHIN brauch ich bei Screenshot
AUCH ALLE ANDEREN Bildschirme.... und das ich dem Agenten sagen kann geh bitte
auf bildschirm 1 oder 2... ZUSAETZLICH muss der voice und auch agent wissen WIE
GROSS das Bild ist, damit der besser klicken kann!"

Zwei Befunde vorab, beide belegt:

* Die Bridge nahm ausschliesslich den HAUPTbildschirm auf
  (``CGDisplayCreateImage(CGMainDisplayID())``) — ein zweiter Monitor war
  unerreichbar.
* Sie berechnet ``image_size`` seit jeher und gab es auch zurueck. Im
  Orchestrator und im Agenten kam es **nirgends** vor: das Modell nannte
  Klickkoordinaten, ohne zu wissen, wie gross das Bild ueberhaupt ist.

Der zweite Punkt ist der heikle: bei einem Nebenbildschirm liegt der Ursprung
NICHT bei 0/0. Ohne Versatz landet jeder Klick auf dem zweiten Monitor auf dem
ersten.
"""

import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
BRIDGE = (WURZEL / "computer-use-bridge/bridge.py").read_text()
VOICE = (WURZEL / "orchestrator/app/services/realtime_voice_session.py").read_text()
MCP = (WURZEL / "agent/mcp/computer-use-server.mjs").read_text()
CUSTOM = (WURZEL / "agent/app/tools/api_client.py").read_text()


class TheBridgeSeesEveryScreenTests(unittest.TestCase):
    def test_it_can_enumerate_the_displays(self):
        self.assertIn("def list_displays(", BRIDGE)
        self.assertIn("CGGetActiveDisplayList", BRIDGE)

    def test_the_primary_screen_is_number_one(self):
        """Das ist die Zaehlweise, die ein Mensch am Telefon benutzt."""
        block = BRIDGE.split("def list_displays(", 1)[1][:2200]
        self.assertIn("d != haupt", block)

    def test_a_specific_screen_can_be_captured(self):
        self.assertIn("def capture_screenshot(scale: float = 1.0, display: int | None = None)", BRIDGE)
        self.assertIn("_capture_macos_inprocess(display_id=None)".replace("=None", ""), BRIDGE.replace("display_id=None", "display_id"))

    def test_an_unknown_screen_number_says_so(self):
        """Lieber eine klare Meldung als stillschweigend den falschen Monitor."""
        block = BRIDGE.split("def capture_screenshot(", 1)[1][:1600]
        self.assertIn("gibt es nicht", block)

    def test_omitting_the_number_keeps_the_old_behaviour(self):
        """Jeder bestehende Aufrufer muss unveraendert weiterlaufen."""
        block = BRIDGE.split("def capture_screenshot(", 1)[1][:1600]
        self.assertIn("if display:", block)


class ClicksLandOnTheRightScreenTests(unittest.TestCase):
    """Der heikelste Teil: der zweite Monitor beginnt nicht bei 0/0."""

    def test_the_offset_is_remembered_after_a_screenshot(self):
        self.assertIn("_coord_offset", BRIDGE)
        block = BRIDGE.split('elif action == "screenshot"', 1)[1][:1800]
        self.assertIn("self._coord_offset", block)

    def test_the_offset_is_added_when_clicking(self):
        block = BRIDGE.split("def _to_click_space", 1)[1][:500]
        # Seit dem Klick-Fix schlaegt ein ausdruecklich genannter Bildschirm den
        # zuletzt aufgenommenen — der Versatz kommt weiter aus einem der beiden.
        self.assertIn("self._display_offset(display) or self._coord_offset", block)
        self.assertIn("+ ox", block)

    def test_and_subtracted_on_the_way_back(self):
        """Sonst waeren die beiden Richtungen nicht mehr Umkehrungen
        voneinander — und `find_element` klickte daneben."""
        block = BRIDGE.split("def _to_image_space", 1)[1][:400]
        self.assertIn("- ox", block)


class EveryRuntimeIsToldTheSizeTests(unittest.TestCase):
    """Die Angabe existierte und ging auf jedem Weg verloren. Drei Laufzeiten —
    an diesem Wochenende ist schon dreimal eine davon vergessen worden."""

    def test_the_voice_front_says_it(self):
        self.assertIn("_bildschirm_hinweis", VOICE)
        block = VOICE.split("def _bildschirm_hinweis", 1)[1][:1800]
        self.assertIn("image_size", block)
        self.assertIn("displays", block)

    def test_the_mcp_runtime_says_it(self):
        self.assertIn("result.image_size", MCP)
        self.assertIn("result.displays", MCP)

    def test_the_custom_llm_runtime_says_it(self):
        block = CUSTOM.split('if action == "screenshot"', 1)[1][:2200]
        self.assertIn('payload.get("image_size")', block)
        self.assertIn('payload.get("displays")', block)

    def test_all_three_explain_where_the_origin_is(self):
        """„1280 breit" allein hilft nicht, wenn unklar ist, wo (0,0) liegt."""
        self.assertIn("oben links", VOICE)
        self.assertIn("top left", MCP)
        self.assertIn("top left", CUSTOM)

    def test_the_screen_list_only_appears_with_more_than_one(self):
        """Bei einem einzigen Monitor waere der Hinweis nur Rauschen im
        Kontext."""
        for name, quelle, marke in (
            ("voice", VOICE, "len(bildschirme) > 1"),
            ("mcp", MCP, "monitore.length > 1"),
            ("custom", CUSTOM, "len(monitore) > 1"),
        ):
            with self.subTest(laufzeit=name):
                self.assertIn(marke, quelle)


class TheScreenCanBeChosenEverywhereTests(unittest.TestCase):
    def test_the_voice_tool_takes_a_display(self):
        # Die Beschreibung ist mit dem Klick-Fix laenger geworden; das
        # Eigenschaftsfeld liegt entsprechend weiter hinten.
        block = VOICE.split("DESKTOP_TOOL = {", 1)[1][:5000]
        self.assertIn('"display"', block)

    def test_the_voice_path_forwards_it(self):
        self.assertIn('params["display"] = int(display)', VOICE)

    def test_the_mcp_tool_takes_a_display(self):
        self.assertIn("screenshotParams.display", MCP)

    def test_the_custom_llm_docs_mention_it(self):
        from pathlib import Path
        defs = (WURZEL / "agent/app/tools/definitions.py").read_text()
        self.assertIn("display: 2", defs)


if __name__ == "__main__":
    unittest.main()
