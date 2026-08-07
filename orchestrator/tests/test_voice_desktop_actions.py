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
