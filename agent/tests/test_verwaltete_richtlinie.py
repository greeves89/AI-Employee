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
#: Claude Code: OHNE Shell (command + args). Mit Shell dazwischen reichten Variablen
#: aus ~/.claude/settings.json ("env"), etwa CLAUDE_CODE_SHELL_PREFIX, um das
#: Hook-Kommando zu umhuellen (live belegt). Node mit festem Pfad (/opt/build-tools
#: gehoert dem Agenten und steht im PATH vor /usr/bin) und leerer Umgebung.
CLAUDE_KOMMANDO = "/usr/bin/env"
CLAUDE_ARGS = ["-i", "/usr/bin/node", SKRIPT_IM_ABBILD]
#: Codex fuehrt Hooks ueber die Shell aus; der Runner schreibt dessen Konfiguration
#: bei jedem Lauf neu, der Agent kann dort keine Umgebung einschleusen.
CODEX_KOMMANDO = f'/usr/bin/env -i AI_EMPLOYEE_LAUF="$AI_EMPLOYEE_LAUF" /usr/bin/node {SKRIPT_IM_ABBILD}'
#: Was das erste Programm (/usr/bin/env) Code oder Pfade laden laesst: aus der Liste,
#: die glibc selbst als unsicher fuehrt (sysdeps/generic/unsecvars.h), plus
#: Shell-Startdateien und Claude Codes Kommando-Umhuellung. Verwaltete Werte gewinnen
#: gegen die des Nutzers. NICHT dabei: Schalter, die schon durch ihr Vorhandensein
#: wirken (LD_SHOW_AUXV, LD_BIND_NOW, ...) — leer gesetzt waeren sie AN (live gesehen).
GLIBC_UNSICHER = {
    "GCONV_PATH", "GETCONF_DIR", "GLIBC_TUNABLES", "HOSTALIASES", "LD_AUDIT", "LD_DEBUG",
    "LD_DEBUG_OUTPUT", "LD_HWCAP_MASK", "LD_LIBRARY_PATH", "LD_ORIGIN_PATH", "LD_PRELOAD",
    "LD_PROFILE", "LOCALDOMAIN", "LOCPATH", "MALLOC_TRACE", "NIS_PATH", "NLSPATH",
    "RESOLV_HOST_CONF", "RES_OPTIONS", "TMPDIR", "TZDIR",
}
VORHANDENSEIN_SCHALTET = {"LD_SHOW_AUXV", "LD_BIND_NOW", "LD_BIND_NOT", "LD_DYNAMIC_WEAK", "LD_USE_LOAD_BIAS"}
LADER_VARIABLEN = GLIBC_UNSICHER | {"BASH_ENV", "ENV", "CLAUDE_CODE_SHELL_PREFIX"}


class ClaudeCodeRichtlinie(unittest.TestCase):
    def setUp(self):
        self.r = json.loads((RICHTLINIE / "managed-settings.json").read_text())

    def test_nur_verwaltete_hooks(self):
        self.assertIs(self.r["allowManagedHooksOnly"], True)

    def test_lader_variablen_des_nutzers_werden_geleert(self):
        env = self.r["env"]
        self.assertEqual({k for k, v in env.items() if v == ""}, LADER_VARIABLEN)
        self.assertFalse(VORHANDENSEIN_SCHALTET & set(env))

    def test_jeder_werkzeugaufruf_geht_durch_das_skript(self):
        gruppe = self.r["hooks"]["PreToolUse"][0]
        self.assertEqual(gruppe["matcher"], "*")
        hook = gruppe["hooks"][0]
        # Kommando statt http: ein http-Hook laesst bei nicht erreichbarem Server
        # durch (live geprueft, auch mit onFailure); das Skript lehnt dann ab.
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"], CLAUDE_KOMMANDO)
        self.assertEqual(hook["args"], CLAUDE_ARGS)

    def test_skript_wartet_kuerzer_als_die_cli(self):
        """Bricht die CLI den Hook wegen Zeitueberschreitung ab, blockiert er nicht."""
        hook = self.r["hooks"]["PreToolUse"][0]["hooks"][0]
        skript = (AGENT / "mcp" / "pretooluse-hook.mjs").read_text()
        ms = int(re.search(r"const HOOK_TIMEOUT_MS = ([\d_]+);", skript).group(1).replace("_", ""))
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
        self.assertEqual(hook["command"], CODEX_KOMMANDO)
        # Das Skript liegt unter managed_dir.
        self.assertTrue(SKRIPT_IM_ABBILD.startswith(self.r["hooks"]["managed_dir"].rstrip("/") + "/"))


class Abbild(unittest.TestCase):
    def test_dockerfile_legt_beide_richtlinien_root_eigen_ab(self):
        df = (AGENT / "Dockerfile").read_text()
        self.assertIn("COPY richtlinie/managed-settings.json /etc/claude-code/managed-settings.json", df)
        self.assertIn("COPY richtlinie/requirements.toml /etc/codex/requirements.toml", df)
        # Kein chown auf den Agenten fuer /etc/claude-code, /etc/codex oder das Skript.
        self.assertNotRegex(df, r"chown[^\n]*(/etc/claude-code|/etc/codex|pretooluse-hook)")

    def test_skript_ist_ausfuehrbar_und_startet_node_mit_festem_pfad(self):
        skript = AGENT / "mcp" / "pretooluse-hook.mjs"
        self.assertTrue(skript.read_text().startswith("#!/usr/bin/node\n"))
        self.assertTrue(os.access(skript, os.X_OK))

    def test_ablehnung_per_exit_code_2(self):
        """JSON auf stdout ist zerbrechlich: unlesbar heisst fuer die CLI "kein Einwand"."""
        skript = (AGENT / "mcp" / "pretooluse-hook.mjs").read_text()
        self.assertIn("process.exit(2)", skript)
        self.assertNotIn("process.stdout.write", skript)

    def test_adresse_und_wartezeit_kommen_nicht_aus_der_umgebung(self):
        """Die Umgebung des Hooks erbt der Agent von seiner CLI."""
        skript = (AGENT / "mcp" / "pretooluse-hook.mjs").read_text()
        self.assertNotIn("AGENT_HOOK_URL", skript)
        self.assertNotIn("AGENT_HOOK_TIMEOUT_MS", skript)


if __name__ == "__main__":
    unittest.main()
