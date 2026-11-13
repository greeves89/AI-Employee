"""Protokoll aus einem Teams-Transkript: ein Zug ohne Werkzeuge, in jeder Laufzeit.

Das Transkript ist fremder Text von Teilnehmenden. Steht darin eine Anweisung, darf sie nichts
ausführen, nichts lesen und nichts wegschicken können. Live auf dem Pi geprüft (13.11.2026):
Claude Code 2.1.323 mit ``--tools "" --strict-mcp-config`` führt keinen Befehl aus (ein Zug);
Codex 0.169 mit ``--sandbox read-only`` startet im Behälter keinen Befehl (bwrap) — fail closed.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app import message_consumer as mc


class _Proz:
    returncode = 0
    pid = 4242


class BefehlszeileTests(unittest.TestCase):
    def _lauf(self, modus: str, **kw) -> list[str]:
        gesehen: list[list[str]] = []

        async def starten(*cmd, **_k):
            gesehen.append(list(cmd))
            return _Proz()

        ausgabe = (b'{"type":"turn.completed","usage":{}}\n' if modus == "codex_cli"
                   else json.dumps({"result": "## Zusammenfassung\nx"}).encode())
        c = mc.MessageConsumer.__new__(mc.MessageConsumer)
        c.agent_id = "a1"
        c._process = None
        c._lauf_kosten = 0.0
        with patch.object(mc.settings, "agent_mode", modus), \
                patch.object(mc.settings, "anthropic_api_key", "x"), \
                patch("app.richtlinie.sicherstellen", lambda: None), \
                patch.object(mc.asyncio, "create_subprocess_exec", starten), \
                patch.object(mc, "communicate_with_idle_timeout", AsyncMock(return_value=(ausgabe, b""))):
            asyncio.run(c._execute_cli_roh("Protokoll bitte", None, **kw))
        return gesehen[0]

    def test_claude_ohne_werkzeuge_und_ohne_mcp(self):
        cmd = self._lauf("claude_code", ohne_werkzeuge=True)
        i = cmd.index("--tools")
        self.assertEqual(cmd[i + 1], "", "leere Liste = keine eingebauten Werkzeuge")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("--mcp-config", cmd)

    def test_claude_normal_unveraendert(self):
        cmd = self._lauf("claude_code")
        self.assertNotIn("--tools", cmd)
        self.assertNotIn("--strict-mcp-config", cmd)

    def test_codex_schreibgeschuetzt_ohne_mcp_und_suche(self):
        cmd = self._lauf("codex_cli", ohne_werkzeuge=True)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        i = cmd.index("--sandbox")
        self.assertEqual(cmd[i + 1], "read-only")
        self.assertIn("mcp_servers={}", cmd)
        self.assertIn('web_search="disabled"', cmd)

    def test_codex_normal_unveraendert(self):
        cmd = self._lauf("codex_cli")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertNotIn("--sandbox", cmd)


class BesprechungsZugTests(unittest.TestCase):
    """Das Feld der Nachricht kommt im Zug an, ohne den Vorspann (knowledge.md lesen)."""

    def _zug(self, nachricht: dict):
        aufrufe = []

        async def cli(prompt, model=None, **kw):
            aufrufe.append((prompt, kw))
            return "## Zusammenfassung\nok"

        c = mc.MessageConsumer.__new__(mc.MessageConsumer)
        c.agent_id = "a1"
        c._execute_cli = cli
        c.redis = AsyncMock()
        pub = AsyncMock()
        asyncio.run(c._besprechungs_zug(nachricht, pub))
        return aufrufe, c.redis

    def test_protokoll_ohne_werkzeuge(self):
        aufrufe, redis = self._zug({"type": "meeting", "room_id": "teams-x", "prompt": "Transkript …",
                                    "ohne_werkzeuge": True})
        prompt, kw = aufrufe[0]
        self.assertEqual(kw, {"ohne_werkzeuge": True})
        self.assertEqual(prompt, "Transkript …", "kein Vorspann: ohne Werkzeuge nichts zu lesen")
        redis.lpush.assert_awaited_once_with("meeting:teams-x:response:a1", "## Zusammenfassung\nok")

    def test_besprechungsraum_unveraendert_mit_werkzeugen(self):
        with patch("app.runner_hooks.get_approval_rules_prefix", return_value=""):
            aufrufe, _ = self._zug({"type": "meeting", "room_id": "r1", "prompt": "Runde 2"})
        prompt, kw = aufrufe[0]
        self.assertEqual(kw, {})
        self.assertIn("knowledge.md", prompt)
        self.assertTrue(prompt.endswith("Runde 2"))


if __name__ == "__main__":
    unittest.main()
