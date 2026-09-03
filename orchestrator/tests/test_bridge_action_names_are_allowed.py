"""Jeder Aktionsname, den ein Harness an die Bridge schickt, MUSS im
serverseitigen Capability-Allowlist stehen.

Warum es diesen Test gibt (Befund 2026-08-18): Der MCP-Server sendet
`click`/`move`/`scroll`/`get_clipboard`/`set_clipboard`, dazu `find_element`
und `wait_for_element`. Keiner dieser sieben Namen stand in CAPABILITY_GROUPS.
`_action_allowed` ist fail-closed → HTTP 403, bevor der Befehl die Bridge auch
nur erreichte. Fuer Claude-Code-Agenten war damit Klicken, Scrollen und
Element-Suchen gesperrt, waehrend dieselben Faehigkeiten ueber Codex (der die
langen `mouse_*`-Namen nutzt) funktionierten — ein Harness-Bruch, den niemand
sah, weil die Doku dem Modell die Schuld gab ("greift zu oft zu anderen
Mitteln") statt der API.

Der Test liest die Namen aus den QUELLEN, nicht aus einer gepflegten Liste —
eine gepflegte Liste haette denselben Fehler nur an einer zweiten Stelle
wiederholt.
"""

import re
import unittest
from pathlib import Path

from app.api.computer_use import _ACTION_TO_GROUP

ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER = ROOT / "agent/mcp/computer-use-server.mjs"
BRIDGE = ROOT / "computer-use-bridge/bridge.py"


def _actions_sent_by_mcp() -> set[str]:
    """Alles, was computer-use-server.mjs via sendCommand("<action>", …) schickt."""
    src = MCP_SERVER.read_text(encoding="utf-8")
    return set(re.findall(r'sendCommand\(\s*"([a-z_]+)"', src))


def _actions_handled_by_bridge() -> set[str]:
    """Alles, was der Dispatcher der Bridge annimmt — beide Schreibweisen.

    Deckt `action == "x"` und `action in ("x", "y")` ab.
    """
    src = BRIDGE.read_text(encoding="utf-8")
    handled: set[str] = set(re.findall(r'action\s*==\s*"([a-z_]+)"', src))
    for tup in re.findall(r'action\s+in\s+\(([^)]*)\)', src):
        handled.update(re.findall(r'"([a-z_]+)"', tup))
    return handled


class BridgeActionNameParityTests(unittest.TestCase):
    def test_every_mcp_action_is_in_a_capability_group(self):
        """Der Fehler von 2026-08-18: sieben MCP-Namen fehlten im Allowlist."""
        sent = _actions_sent_by_mcp()
        self.assertTrue(sent, "keine sendCommand-Aufrufe gefunden — Regex pruefen")
        missing = sorted(sent - set(_ACTION_TO_GROUP))
        self.assertEqual(
            missing, [],
            "Diese Aktionen sendet der MCP-Server, aber CAPABILITY_GROUPS kennt "
            "sie nicht — _action_allowed ist fail-closed, der Agent bekommt 403: "
            + ", ".join(missing),
        )

    def test_every_mcp_action_is_implemented_by_the_bridge(self):
        """Gegenprobe: ein freigegebener Name, den die Bridge nicht kennt, waere
        genauso tot — nur mit einem anderen Fehlerbild."""
        sent = _actions_sent_by_mcp()
        handled = _actions_handled_by_bridge()
        missing = sorted(sent - handled)
        self.assertEqual(
            missing, [],
            "Der MCP-Server sendet Aktionen, die der Bridge-Dispatcher nicht "
            "annimmt: " + ", ".join(missing),
        )

    def test_click_and_element_lookup_are_reachable(self):
        """Namentlich die Faehigkeiten, deren Sperre den Kundenbefund erzeugt hat.

        Ohne Klicken und Element-Suche ist "den Rechner bedienen" nur Zusehen.
        """
        for action in ("click", "mouse_click", "scroll", "move",
                       "find_element", "wait_for_element"):
            with self.subTest(action=action):
                self.assertIn(
                    action, _ACTION_TO_GROUP,
                    f"'{action}' ist nicht freigegeben — Bedienung bleibt blind",
                )

    def test_sensitive_groups_are_not_on_by_default(self):
        """Neue Gruppen duerfen nicht versehentlich in die Standardfreigabe
        rutschen. Browser und Mikrofon arbeiten in angemeldeten Sitzungen bzw.
        hoeren mit — die gehoeren wie `shell` bewusst eingeschaltet."""
        from app.api.computer_use import DEFAULT_ALLOWED_CAPABILITIES

        for group in ("shell", "clipboard", "input_capture", "voice_capture", "browser", "ego_browser"):
            with self.subTest(group=group):
                self.assertNotIn(group, DEFAULT_ALLOWED_CAPABILITIES)


if __name__ == "__main__":
    unittest.main()
