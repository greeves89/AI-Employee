"""Was die Bridge beim Verbinden meldet, muss dem entsprechen, was sie kann.

Befund 2026-08-18 (live beim Verbinden gesehen): Die Bridge meldete
``caps=[... 'ax_tree', 'find_element', 'wait_for_element']`` — ohne
``list_windows``, ``focus_window`` und ohne jede ``browser_*``-Aktion, obwohl
der Dispatcher sie am selben Tag bekommen hatte. Die Meldung war eine ZWEITE,
handgetippte Liste mitten im Verbindungsaufbau und wurde beim Ergaenzen
schlicht vergessen.

Folge: Die Faehigkeiten liefen, wurden dem Server aber nie angekuendigt — alles,
was "was kann dieser Rechner" fragt (Oberflaeche, Agent), sah sie nicht.

Dieselbe Fehlerklasse wie die CI-Paketliste und wie CAPABILITY_GROUPS: eine
zweite Wahrheit, die still veraltet. Deshalb wird hier gegen die EINZIGE echte
Quelle geprueft — den Dispatcher.
"""

import re
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2] / "computer-use-bridge/bridge.py"

# Aktionen, die der Dispatcher zwar kennt, die aber bewusst nicht angekuendigt
# werden: `ping` ist ein Transport-Detail, kein Koennen.
NICHT_ANGEKUENDIGT = {"ping"}

# Der Dispatcher nimmt fuer dieselbe Faehigkeit zwei Schreibweisen an (der
# MCP-Weg sendet die kurze, der Codex-Weg die lange). Angekuendigt wird EINE
# davon — das ist kein Loch, sondern derselbe Griff unter zwei Namen.
ALIASE = {
    "mouse_click": "click",
    "mouse_move": "move",
    "mouse_scroll": "scroll",
    "clipboard_read": "get_clipboard",
    "clipboard_write": "set_clipboard",
}


def _quelle() -> str:
    return BRIDGE.read_text(encoding="utf-8")


def _dispatcher_actions(src: str) -> set[str]:
    """Jede Faehigkeit, die der Dispatcher annimmt — Aliase zusammengefasst."""
    handled: set[str] = set(re.findall(r'action\s*==\s*"([a-z_]+)"', src))
    for tup in re.findall(r'action\s+in\s+\(([^)]*)\)', src):
        handled.update(re.findall(r'"([a-z_]+)"', tup))
    handled = {ALIASE.get(a, a) for a in handled}
    return handled - NICHT_ANGEKUENDIGT


def _announced(src: str) -> set[str]:
    """BASE_ACTIONS + AX_ACTIONS — was beim Verbinden gemeldet wird."""
    out: set[str] = set()
    for name in ("BASE_ACTIONS", "AX_ACTIONS"):
        block = src.split(f"{name} = [", 1)[1].split("]", 1)[0]
        out.update(re.findall(r'"([a-z_]+)"', block))
    return out


class BridgeAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.src = _quelle()

    def test_everything_the_bridge_can_do_is_announced(self):
        fehlt = sorted(_dispatcher_actions(self.src) - _announced(self.src))
        self.assertEqual(
            fehlt, [],
            "Diese Aktionen kann die Bridge, meldet sie aber nicht: "
            + ", ".join(fehlt),
        )

    def test_nothing_is_announced_that_the_bridge_cannot_do(self):
        """Die Gegenrichtung — eine Zusage ohne Umsetzung ist genauso falsch."""
        zuviel = sorted(_announced(self.src) - _dispatcher_actions(self.src))
        self.assertEqual(
            zuviel, [],
            "Diese Aktionen werden gemeldet, der Dispatcher kennt sie nicht: "
            + ", ".join(zuviel),
        )

    def test_the_announcement_is_not_a_second_hand_typed_list(self):
        """Der Verbindungsaufbau darf die Namen nicht erneut auffuehren."""
        hello = self.src.split("# Announce capabilities", 1)[1][:600]
        self.assertIn("BASE_ACTIONS", hello)
        self.assertNotIn('"screenshot", "click"', hello,
                         "Im hello-Rumpf steht wieder eine getippte Liste")

    def test_element_lookup_is_only_promised_with_the_accessibility_tree(self):
        """Ohne AX-Baum kann die Bridge keine Elemente finden — dann darf sie es
        auch nicht ankuendigen."""
        for action in ("find_element", "wait_for_element", "ax_tree"):
            with self.subTest(action=action):
                block = self.src.split("AX_ACTIONS = [", 1)[1].split("]", 1)[0]
                self.assertIn(action, block)


if __name__ == "__main__":
    unittest.main()
