"""Der Sprachweg kann Apps wirklich bedienen — nicht nur oeffnen.

Der Agent sagte dem Nutzer: „das geht aber nicht ueber die Bridge, weil ich nur die App
oeffnen kann, nicht in ihr navigieren oder klicken". Das war falsch — die Bridge kann
click/type/key/scroll und liest den Bedienungshilfen-Baum. Im Sprachweg fehlten aber
`find` und `key`, und ohne Suche bleibt nur blindes Klicken auf geratene Koordinaten.
"""

import unittest
from pathlib import Path

VOICE = (Path(__file__).resolve().parents[1] / "app/services/realtime_voice_session.py").read_text()
DESKTOP = VOICE.split("async def _desktop", 1)[1].split("\n    async def ", 1)[0]
BRIDGE = (Path(__file__).resolve().parents[2] / "computer-use-bridge/bridge.py").read_text()


class ActionCoverageTests(unittest.TestCase):
    def test_find_maps_to_the_accessibility_tree(self):
        self.assertIn('act, params = "find_element"', DESKTOP)

    def test_keyboard_shortcuts_are_possible(self):
        self.assertIn('act, params = "hotkey"', DESKTOP)
        self.assertIn('text.split("+")', DESKTOP)

    def test_wait_and_scroll_exist(self):
        self.assertIn('act, params = "wait_for_element"', DESKTOP)
        self.assertIn('act, params = "scroll"', DESKTOP)

    def test_every_action_exists_in_the_bridge(self):
        """Kein Werkzeug anbieten, das die Bridge gar nicht kennt."""
        for act in ("find_element", "wait_for_element", "hotkey", "scroll", "mouse_click", "type"):
            self.assertIn(f'"{act}"', BRIDGE, f"{act} fehlt in der Bridge")


class WordingTests(unittest.TestCase):
    def test_the_false_excuse_is_forbidden(self):
        self.assertIn("nur oeffnen, aber nicht navigieren", VOICE)

    def test_the_chain_is_spelled_out(self):
        self.assertIn("SO BEDIENST DU EINE APP", VOICE)


if __name__ == "__main__":
    unittest.main()


class WindowsSupportTests(unittest.TestCase):
    """Windows kann jetzt dasselbe wie macOS — und wo etwas fehlt, wird es gesagt.

    Vorher gab es den Bedienungshilfen-Baum nur auf macOS: unter Windows konnte die
    Bridge nur klicken, wohin jemand zeigte. Jetzt liefert UI Automation denselben
    Baum, und beide Systeme benutzen dieselbe Suche.
    """

    def test_windows_has_its_own_tree_producer(self):
        self.assertIn("def _win_ui_tree(", BRIDGE)
        self.assertIn("import uiautomation as auto", BRIDGE)

    def test_both_platforms_share_ONE_search(self):
        self.assertIn("def search_tree(", BRIDGE)
        self.assertEqual(BRIDGE.count("def _search(node"), 0)
        self.assertEqual(BRIDGE.count("def _find(node"), 0)

    def test_role_names_work_across_platforms(self):
        """`button` muss AXButton (macOS) UND ButtonControl (Windows) treffen."""
        self.assertIn("def _role_matches(", BRIDGE)
        self.assertIn('removeprefix("ax")', BRIDGE)
        self.assertIn('removesuffix("control")', BRIDGE)

    def test_capabilities_are_asked_not_guessed(self):
        self.assertIn("def ax_tree_available(", BRIDGE)
        self.assertIn("if ax_tree_available() else []", BRIDGE)
        self.assertIn('"ax_tree_available": ax_tree_available()', BRIDGE)

    def test_missing_package_is_reported_actionably(self):
        self.assertIn("pip install uiautomation", BRIDGE)
        self.assertIn("pip install uiautomation", VOICE)

    def test_missing_tree_still_leaves_a_workable_answer(self):
        self.assertIn("only available on", VOICE)
        hint = VOICE.split('if "only available on" in why', 1)[1][:400]
        self.assertIn("Screenshot", hint)
        self.assertIn("gehen hier genauso", hint)

    def test_windows_build_ships_the_dependency(self):
        root = Path(__file__).resolve().parents[2]
        self.assertIn("'uiautomation'", (root / "computer-use-bridge/bridge_windows.spec").read_text())
        self.assertIn("uiautomation", (root / "computer-use-bridge/requirements.txt").read_text())
