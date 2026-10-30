"""Der PreToolUse-Hook steht in einer verwalteten, root-eigenen Richtlinie (P0-2).

Vorher stand er in /workspace/.claude/settings.json bzw. ~/.codex/config.toml —
beides kann der Agent selbst schreiben, ein ``echo '{}' >`` per Bash schaltete
die Pruefung ab. Jetzt liegt er in /etc/claude-code/managed-settings.json und
/etc/codex/requirements.toml, mit "nur verwaltete Hooks". Live geprueft
(Claude Code 2.1.312, Codex 0.165): ``disableAllHooks`` in Projekt, Nutzer und
``--settings`` bzw. ``-c features.hooks=false`` schalten ihn nicht ab.

Diese Tests halten fest, dass Abbild, Richtlinie und Skript zusammenpassen.
"""

import json
import os
import re
import tomllib
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
RICHTLINIE = AGENT / "richtlinie"
SKRIPT_IM_ABBILD = "/opt/mcp/pretooluse-hook.mjs"


class ClaudeCodeRichtlinie(unittest.TestCase):
    def setUp(self):
        self.r = json.loads((RICHTLINIE / "managed-settings.json").read_text())

    def test_nur_verwaltete_hooks(self):
        self.assertIs(self.r["allowManagedHooksOnly"], True)

    def test_jeder_werkzeugaufruf_geht_durch_das_skript(self):
        gruppe = self.r["hooks"]["PreToolUse"][0]
        self.assertEqual(gruppe["matcher"], "*")
        hook = gruppe["hooks"][0]
        # Kommando statt http: ein http-Hook laesst bei nicht erreichbarem Server
        # durch (live geprueft, auch mit onFailure); das Skript lehnt dann ab.
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"], SKRIPT_IM_ABBILD)

    def test_skript_wartet_kuerzer_als_die_cli(self):
        """Bricht die CLI den Hook wegen Zeitueberschreitung ab, blockiert er nicht."""
        hook = self.r["hooks"]["PreToolUse"][0]["hooks"][0]
        skript = (AGENT / "mcp" / "pretooluse-hook.mjs").read_text()
        ms = int(re.search(r"\|\|\s*([\d_]+)", skript.split("HOOK_TIMEOUT_MS =")[1]).group(1).replace("_", ""))
        self.assertLess(ms / 1000, hook["timeout"])


class CodexRichtlinie(unittest.TestCase):
    def setUp(self):
        with open(RICHTLINIE / "requirements.toml", "rb") as fh:
            self.r = tomllib.load(fh)

    def test_nur_verwaltete_hooks_und_festgenagelt(self):
        self.assertIs(self.r["allow_managed_hooks_only"], True)
        self.assertIs(self.r["features"]["hooks"], True)

    def test_jeder_werkzeugaufruf_geht_durch_das_skript(self):
        gruppe = self.r["hooks"]["PreToolUse"][0]
        self.assertEqual(gruppe["matcher"], ".*")
        hook = gruppe["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"], SKRIPT_IM_ABBILD)
        # Verwaltete Kommandos liegen unter managed_dir.
        self.assertTrue(hook["command"].startswith(self.r["hooks"]["managed_dir"].rstrip("/") + "/"))


class Abbild(unittest.TestCase):
    def test_dockerfile_legt_beide_richtlinien_root_eigen_ab(self):
        df = (AGENT / "Dockerfile").read_text()
        self.assertIn("COPY richtlinie/managed-settings.json /etc/claude-code/managed-settings.json", df)
        self.assertIn("COPY richtlinie/requirements.toml /etc/codex/requirements.toml", df)
        # Kein chown auf den Agenten fuer /etc/claude-code, /etc/codex oder das Skript.
        self.assertNotRegex(df, r"chown[^\n]*(/etc/claude-code|/etc/codex|pretooluse-hook)")

    def test_skript_ist_ausfuehrbar_und_startet_node(self):
        skript = AGENT / "mcp" / "pretooluse-hook.mjs"
        self.assertTrue(skript.read_text().startswith("#!/usr/bin/env node"))
        self.assertTrue(os.access(skript, os.X_OK))


if __name__ == "__main__":
    unittest.main()
