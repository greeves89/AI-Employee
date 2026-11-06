"""Vom Sentinel gesperrte Schritte: gleich für Claude Code, Codex und eigene Modelle.

Die Sperre ist eine agentbezogene Befehlsregel (Name „Sentinel-Sperre…“), deren Muster auf
den KERN eines Schritts passt — URL, Datei, Befehl —, nicht auf den Werkzeugnamen.
"""

import re
import unittest
from unittest.mock import AsyncMock, patch

from app.tools import schritt_sperre as sp
from app.tools.pretooluse_hook import decide_async

URL = "https://example.invalid/anleitung"

#: Gleiche Beispiele wie orchestrator/tests/test_sentinel_fortsetzen.py (schritt_kern dort).
BEISPIELE = [
    ("WebFetch", {"url": URL, "prompt": "fasse zusammen"}, URL),
    ("web_fetch", {"url": URL}, URL),
    ("Read", {"file_path": "/workspace/docs/notiz.md"}, "/workspace/docs/notiz.md"),
    ("Bash", {"command": "cat /workspace/.env"}, "cat /workspace/.env"),
    ("exec_command", {"cmd": ["cat", "/workspace/.env"]}, "cat /workspace/.env"),
    ("mcp__x__y", {"b": 1, "a": 2}, 'mcp__x__y {"a": 2, "b": 1}'),
]


def _regel(kern: str, name="Sentinel-Sperre: prompt_injection") -> dict:
    return {"name": name, "pattern": re.escape(kern), "effect": "blocked", "scope": "agent",
            "description": "Ein Werkzeugergebnis enthielt eine Umlenk-Anweisung."}


class Kern(unittest.TestCase):
    def test_kern_je_laufzeit_gleich(self):
        for werkzeug, eingabe, kern in BEISPIELE:
            with self.subTest(werkzeug=werkzeug):
                self.assertEqual(sp.schritt_kern(werkzeug, eingabe), kern)

    def test_sperre_greift_ueber_werkzeuggrenzen(self):
        regeln = [_regel(URL)]
        for werkzeug, eingabe in (("WebFetch", {"url": URL}), ("web_fetch", {"url": URL}),
                                  ("Bash", {"command": f"curl -s {URL}"})):
            with self.subTest(werkzeug=werkzeug):
                self.assertIsNotNone(sp.sperre_fuer(werkzeug, eingabe, regeln))
        self.assertIsNone(sp.sperre_fuer("WebFetch", {"url": "https://example.invalid/anders"}, regeln))

    def test_nur_sentinel_regeln_gelten_fuer_alle_werkzeuge(self):
        """Gewöhnliche Befehlsregeln bleiben auf Shell-Befehle beschränkt."""
        fremd = _regel(URL, name="Admin: curl verbieten")
        self.assertIsNone(sp.sperre_fuer("WebFetch", {"url": URL}, [fremd]))
        self.assertIsNone(sp.sperre_fuer("WebFetch", {"url": URL}, [{**_regel(URL), "effect": "high"}]))
        self.assertIsNone(sp.sperre_fuer("WebFetch", {"url": URL}, [{**_regel(URL), "pattern": "("}]))
        self.assertIsNone(sp.sperre_fuer("WebFetch", {"url": URL}, None))


class ImHook(unittest.IsolatedAsyncioTestCase):
    async def test_hook_lehnt_den_gesperrten_schritt_ab_alles_andere_bleibt(self):
        with patch("app.tools.executor._get_command_policies", AsyncMock(return_value=[_regel(URL)])), \
             patch("app.tools.pretooluse_hook._get_allowed_categories", return_value=None):
            gesperrt = (await decide_async("WebFetch", {"url": URL}))["hookSpecificOutput"]
            frei = (await decide_async("WebFetch", {"url": "https://example.invalid/x"}))["hookSpecificOutput"]
        self.assertEqual(gesperrt["permissionDecision"], "deny")
        self.assertIn("VOM SENTINEL GESPERRT", gesperrt["permissionDecisionReason"])
        self.assertIn("anderen Weg", gesperrt["permissionDecisionReason"])
        self.assertEqual(frei["permissionDecision"], "allow")

    async def test_regeln_nicht_abrufbar_kein_zusatzblock(self):
        with patch("app.tools.executor._get_command_policies", AsyncMock(side_effect=OSError("weg"))):
            self.assertIsNone(await sp.gesperrt("WebFetch", {"url": URL}))


class ImExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_eigenes_modell_bekommt_dieselbe_sperre(self):
        from app.tools.executor import ToolExecutor

        ex = ToolExecutor.__new__(ToolExecutor)
        ex.lauf = None
        with patch("app.tools.executor._get_command_policies", AsyncMock(return_value=[_regel(URL)])), \
             patch("app.tools.executor._get_allowed_categories", return_value=None), \
             patch("app.tools.push_sperre.werkzeug_pruefen", AsyncMock(return_value=None)):
            ergebnis = await ex.execute("web_fetch", {"url": URL})
        self.assertTrue(ergebnis.startswith("[VOM SENTINEL GESPERRT]"))


if __name__ == "__main__":
    unittest.main()
