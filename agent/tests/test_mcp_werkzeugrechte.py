"""Werkzeugrechte für alle MCP-Server, in allen Laufzeiten (#781 PR 2).

Codex hat weder Freigabe-Schalter noch Hooks; der einzige echte Abfangpunkt für
seine MCP-Werkzeuge ist der Server selbst. Jeder eingebaute MCP-Server fragt
deshalb vor jedem Werkzeugaufruf den lokalen PreToolUse-Hook (``_tool_gate.mjs``).

Das ist nur so gut wie die Zuordnung dahinter: ein Werkzeug ohne Kategorie gilt
bei konfigurierter Freigabeliste als "unbekannt" und ist gesperrt. Bis zu dieser
Prüfung waren es 54 (Browser, E-Mail, Microsoft 365, Videos) — gesperrt auch für
Claude-Code-Agenten mit eingeschränkter Autonomie.
"""

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tools import pretooluse_hook as hook

MCP = Path(__file__).resolve().parents[1] / "mcp"

# Der Freigabe-Server ist selbst der Weg zur Freigabe — ihn zu sperren hiesse,
# einem Agenten das Fragen zu verbieten. memory-server prüft im Handler selbst
# (PR 1, eigene Tests in memory-server.test.mjs).
_OHNE_GATEAKTIVIEREN = {"bash-approval-server.mjs", "memory-server.mjs"}

_ALLE_KATEGORIEN = {
    "file_read", "file_write", "shell_exec", "system_config", "web_search",
    "custom", "external_communication", "knowledge_write", "purchase",
}


def _server_dateien():
    return sorted(p for p in MCP.glob("*-server*.mjs") if ".test." not in p.name)


def _werkzeuge(pfad: Path) -> set[str]:
    return set(re.findall(r'^\s*name:\s*"([a-z][a-z0-9_]+)"', pfad.read_text(), re.M))


def _entscheidung(werkzeug: str, erlaubt: set[str] | None) -> str:
    with patch.object(hook, "_get_allowed_categories", return_value=erlaubt):
        return hook.decide(f"mcp__x__{werkzeug}")["hookSpecificOutput"]["permissionDecision"]


class JedesWerkzeugHatEineKategorie(unittest.TestCase):
    def test_die_suche_findet_ueberhaupt_werkzeuge(self):
        """Sonst bestünde der nächste Test, weil er nichts prüft."""
        alle = set().union(*(_werkzeuge(p) for p in _server_dateien()))
        self.assertGreater(len(alle), 100)
        for erwartet in ("browser_navigate", "brain_contribute", "memory_save", "render_video"):
            self.assertIn(erwartet, alle)

    def test_kein_werkzeug_ist_unbekannt(self):
        """Mit allen Kategorien freigegeben darf nichts gesperrt sein — gesperrt
        wäre dann nur noch, was in keiner Tabelle steht."""
        unbekannt = {
            f"{p.name}: {w}"
            for p in _server_dateien()
            for w in _werkzeuge(p)
            if _entscheidung(w, set(_ALLE_KATEGORIEN)) == "deny"
        }
        self.assertEqual(unbekannt, set())


class KategorienGreifen(unittest.TestCase):
    def test_lesen_ohne_senden(self):
        nur_lesen = {"file_read", "web_search"}
        for w in ("email_read", "email_search", "ms_list_calendar_events", "ms_read_file_content",
                  "ms_graph_get", "ex_list_emails", "list_videos"):
            self.assertEqual(_entscheidung(w, nur_lesen), "allow", w)
        for w in ("email_send", "email_reply", "ms_send_teams_message", "ms_cancel_event",
                  "ms_excel_write", "ex_send_email", "browser_navigate", "ego_click", "render_video"):
            self.assertEqual(_entscheidung(w, nur_lesen), "deny", w)

    def test_senden_braucht_die_m365_faehigkeit(self):
        self.assertEqual(_entscheidung("email_send", {"file_read", "custom"}), "allow")

    def test_ohne_regeln_ist_alles_frei(self):
        self.assertEqual(_entscheidung("email_send", None), "allow")

    def test_buchfuehrung_ist_immer_frei(self):
        self.assertEqual(_entscheidung("skill_record_usage", set()), "allow")


class JederServerHatDasGate(unittest.TestCase):
    def test_gate_steht_direkt_nach_dem_server(self):
        for pfad in _server_dateien():
            if pfad.name in _OHNE_GATEAKTIVIEREN:
                continue
            with self.subTest(server=pfad.name):
                text = pfad.read_text()
                self.assertIn('from "./_tool_gate.mjs"', text)
                self.assertRegex(text, r"new Server\([\s\S]*?\n  \);\n  gateAktivieren\(server, \"[a-z-]+\"\);")

    def test_memory_server_prueft_selbst(self):
        self.assertIn("checkToolPermission(", (MCP / "memory-server.mjs").read_text())


if __name__ == "__main__":
    unittest.main()


class CodexHook(unittest.TestCase):
    """#781 PR 3: Codex schickt Shell, Dateiaenderungen und MCP-Werkzeuge durch
    einen PreToolUse-Hook (live geprueft mit codex-cli 0.159). Die Konfiguration
    muss ihn fuer JEDES Werkzeug eintragen, und die Entscheidung muss Codex'
    eigene Werkzeugnamen kennen."""

    def _konfiguration(self) -> dict:
        import tempfile
        import tomllib

        from app import codex_runner

        ordner = tempfile.mkdtemp()
        codex_runner._ensure_codex_mcp_config(ordner, {"AGENT_ID": "a1", "AGENT_TOKEN": "t"})
        with open(Path(ordner) / "config.toml", "rb") as fh:
            return tomllib.load(fh)

    def test_hook_steht_nicht_in_der_selbst_beschreibbaren_konfiguration(self):
        """~/.codex/config.toml kann der Agent selbst schreiben — ein Hook dort waere
        abschaltbar. Er kommt verwaltet aus /etc/codex/requirements.toml."""
        konfig = self._konfiguration()
        self.assertNotIn("hooks", konfig)
        # Der Rest der Datei bleibt gueltig und vollstaendig (hier: Projektvertrauen;
        # die MCP-Server fehlen lokal nur, weil /opt/mcp nicht existiert).
        self.assertEqual(konfig["projects"]["/workspace"]["trust_level"], "trusted")

    def test_codex_shell_und_dateiaenderung_brauchen_ihre_kategorie(self):
        nur_lesen = {"file_read", "web_search"}
        self.assertEqual(_entscheidung_nativ("Bash", nur_lesen), "deny")
        self.assertEqual(_entscheidung_nativ("apply_patch", nur_lesen), "deny")
        self.assertEqual(_entscheidung_nativ("apply_patch", nur_lesen | {"file_write"}), "allow")
        self.assertEqual(_entscheidung_nativ("exec_command", nur_lesen | {"shell_exec"}), "allow")

    def test_codex_meta_werkzeuge_sind_frei(self):
        for w in ("update_plan", "view_image", "spawn_agent", "request_user_input", "tool_search"):
            self.assertEqual(_entscheidung_nativ(w, set()), "allow", w)


def _entscheidung_nativ(werkzeug: str, erlaubt: set[str] | None) -> str:
    with patch.object(hook, "_get_allowed_categories", return_value=erlaubt):
        return hook.decide(werkzeug)["hookSpecificOutput"]["permissionDecision"]


class LaufzeitKonfigurationIstGeschuetzt(unittest.TestCase):
    """Bis P0-2 stand DIESES Gate in /workspace/.claude/settings.json, und Codex
    fuehrt /workspace/.codex/hooks.json aus (live geprueft). Das Gate liegt jetzt
    verwaltet unter /etc; eigene Hooks und Befehle dort bleiben trotzdem heikel.
    Ein Agent, der nur Dateien schreiben darf, haette darueber das Gate entfernen
    oder eigene Befehle ausfuehren koennen. Schreiben dort braucht system_config."""

    nur_schreiben = {"file_read", "web_search", "file_write"}

    def _nativ(self, werkzeug, eingabe, erlaubt):
        with patch.object(hook, "_get_allowed_categories", return_value=erlaubt):
            return hook.decide(werkzeug, eingabe)["hookSpecificOutput"]["permissionDecision"]

    def test_claude_settings_bearbeiten_braucht_system_config(self):
        for werkzeug in ("Write", "Edit"):
            self.assertEqual(self._nativ(werkzeug, {"file_path": "/workspace/.claude/settings.json"},
                                         self.nur_schreiben), "deny", werkzeug)
        self.assertEqual(self._nativ("Edit", {"file_path": "/workspace/.claude/settings.json"},
                                     self.nur_schreiben | {"system_config"}), "allow")

    def test_codex_hook_per_patch_anlegen_braucht_system_config(self):
        patch_text = "*** Begin Patch\n*** Add File: /workspace/.codex/hooks.json\n+{}\n*** End Patch"
        self.assertEqual(self._nativ("apply_patch", {"command": patch_text}, self.nur_schreiben), "deny")

    def test_gewoehnliche_dateien_bleiben_dateischreiben(self):
        patch_text = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"
        self.assertEqual(self._nativ("apply_patch", {"command": patch_text}, self.nur_schreiben), "allow")
        self.assertEqual(self._nativ("Write", {"file_path": "/workspace/claude-notizen.md"},
                                     self.nur_schreiben), "allow")

    def test_ohne_regeln_bleibt_alles_frei(self):
        self.assertEqual(self._nativ("Write", {"file_path": "/workspace/.claude/settings.json"}, None), "allow")

    def test_skills_befehle_und_subagenten_unter_claude_sind_geschuetzt(self):
        """Ihr Kopf kann eigene Hooks tragen, Befehle koennen per !`…` Shell
        ausfuehren — Schreiben dort braucht system_config. Eigene Skills legt ein
        Agent ohne diese Faehigkeit ueber create_skill an (immer erlaubt)."""
        for pfad in ("/workspace/.claude/skills/video/SKILL.md", "/workspace/.claude/commands/neu.md",
                     "/workspace/.claude/agents/helfer.md", "/workspace/.claude/hooks/x.sh"):
            self.assertEqual(self._nativ("Write", {"file_path": pfad}, self.nur_schreiben), "deny", pfad)
            self.assertEqual(self._nativ("Write", {"file_path": pfad}, self.nur_schreiben | {"system_config"}),
                             "allow", pfad)
        self.assertEqual(_entscheidung("create_skill", set()), "allow")

    def test_projekt_mcp_server_braucht_system_config(self):
        """.mcp.json und ~/.claude.json (mcpServers) starten Programme — wie ein Hook."""
        for pfad in ("/workspace/.mcp.json", "/home/agent/.claude.json",
                     "/workspace/.git/config", "/workspace/projekt/.git/hooks/pre-commit",
                     "/home/agent/.gitconfig"):
            self.assertEqual(self._nativ("Write", {"file_path": pfad}, self.nur_schreiben), "deny", pfad)
            self.assertEqual(self._nativ("Edit", {"file_path": pfad}, self.nur_schreiben), "deny", pfad)

    def test_codex_subagenten_mit_namensraum_sind_frei(self):
        for w in ("collaborationspawn_agent", "collaborationwait_agent"):
            self.assertEqual(self._nativ(w, {}, set()), "allow", w)


class SchreibenNurInDenArbeitsbereichen(unittest.TestCase):
    """Erlaubnis statt Sperrliste: ausserhalb von /workspace, /shared, /mnt, /tmp
    (Home mit Shell-Konfiguration, site-packages, Systempfade) braucht
    system_config — eine Sperrliste ausfuehrbarer Orte wird nie vollstaendig."""

    nur_schreiben = {"file_read", "web_search", "file_write"}

    def _write(self, pfad, erlaubt):
        with patch.object(hook, "_get_allowed_categories", return_value=erlaubt):
            return hook.decide("Write", {"file_path": pfad})["hookSpecificOutput"]["permissionDecision"]

    def test_ausserhalb_braucht_system_config(self):
        for pfad in ("~/.bashrc", "/home/agent/.profile", "/root/.bashrc",
                     "/root/.local/lib/python3.12/site-packages/x.pth",
                     "/usr/lib/python3/dist-packages/sitecustomize.py", "/etc/cron.d/x",
                     "/workspace/../home/agent/.bashrc", "/opt/mcp/pretooluse-hook.mjs"):
            self.assertEqual(self._write(pfad, self.nur_schreiben), "deny", pfad)
            self.assertEqual(self._write(pfad, self.nur_schreiben | {"system_config"}), "allow", pfad)

    def test_in_den_arbeitsbereichen_reicht_dateischreiben(self):
        for pfad in ("/workspace/bericht.md", "src/app.py", "/shared/uebergabe/plan.md",
                     "/mnt/brains/projektwissen/notiz.md", "/tmp/zwischenstand.txt"):
            self.assertEqual(self._write(pfad, self.nur_schreiben), "allow", pfad)

    def test_ohne_regeln_bleibt_alles_frei(self):
        self.assertEqual(self._write("~/.bashrc", None), "allow")


class CustomLlmHatDieselbeRegel(unittest.IsolatedAsyncioTestCase):
    """Harness-Paritaet: die custom_llm-Laufzeit fragt keinen Hook, sondern prueft
    im ToolExecutor — dort galt der Schreibschutz bis v1.365.5 gar nicht."""

    async def test_write_file_ausserhalb_wird_gesperrt_und_nichts_geschrieben(self):
        """„Ausserhalb" muss der Test SELBST herstellen, nicht von der Lage des
        Temp-Ordners borgen: ``tempfile.mkdtemp()`` liefert auf Linux ``/tmp/…``
        — und ``/tmp`` ist ein erlaubter Arbeitsbereich. Der Test lief deshalb
        nur auf macOS (dort ``/var/folders/…``) und war auf dem Bauknecht rot,
        ohne dass die Regel selbst etwas falsch machte. Also wird die Liste der
        Arbeitsbereiche hier festgelegt: genau der Arbeitsordner, nichts sonst.
        """
        import os
        import tempfile

        from app.tools import executor as ex

        arbeitsordner = tempfile.mkdtemp()
        ziel = Path(tempfile.mkdtemp()) / "home" / ".bashrc"
        with patch.object(ex, "_get_allowed_categories", return_value={"file_read", "file_write"}), \
                patch("app.tools.schreibschutz._ARBEITSBEREICHE",
                      (os.path.realpath(arbeitsordner),)):
            ausfuehrer = ex.ToolExecutor(workspace_dir=arbeitsordner)
            antwort = await ausfuehrer.execute("write_file", {"path": str(ziel), "content": "boese"})
        self.assertIn("AUTONOMY BLOCK", antwort)
        self.assertFalse(ziel.exists())

    async def test_write_file_im_arbeitsordner_geht(self):
        from app.tools import executor as ex

        import os
        import tempfile

        # Dieselbe Festlegung wie im Test darueber, statt eine Liste moeglicher
        # Temp-Orte je Betriebssystem zu pflegen.
        ordner = tempfile.mkdtemp()
        # ``WORKSPACE_DIR`` mit: ein RELATIVER Pfad wird gegen diese Variable
        # aufgeloest, nicht gegen ``workspace_dir`` des Ausfuehrers. Im Betrieb
        # ist beides derselbe Ordner — im Test muss man es sagen.
        with patch.object(ex, "_get_allowed_categories", return_value={"file_read", "file_write"}), \
                patch.dict(os.environ, {"WORKSPACE_DIR": ordner}), \
                patch("app.tools.schreibschutz._ARBEITSBEREICHE", (os.path.realpath(ordner),)):
            ausfuehrer = ex.ToolExecutor(workspace_dir=ordner)
            antwort = await ausfuehrer.execute("write_file", {"path": "notiz.md", "content": "ok"})
        self.assertNotIn("AUTONOMY BLOCK", antwort)


class BefehlsregelnFuerAlleShellNamen(unittest.IsolatedAsyncioTestCase):
    async def test_codex_interne_shell_namen_laufen_durch_die_befehlsregeln(self):
        async def _blockiert(befehl):
            return ("blocked", "rm verboten") if "rm -rf" in befehl else (None, None)

        with patch.object(hook, "_get_allowed_categories", return_value=None), \
                patch.object(hook, "_evaluate_command_policy", _blockiert):
            for werkzeug, eingabe in (("Bash", {"command": "rm -rf /"}),
                                      ("exec_command", {"cmd": "rm -rf /"}),
                                      ("shell_command", {"command": ["sh", "-c", "rm -rf /"]})):
                aus = await hook.decide_async(werkzeug, eingabe)
                self.assertEqual(aus["hookSpecificOutput"]["permissionDecision"], "deny", werkzeug)


class GateStehtVorJedemHandler(unittest.TestCase):
    def test_gate_vor_dem_ersten_set_request_handler(self):
        for pfad in _server_dateien():
            if pfad.name in _OHNE_GATEAKTIVIEREN:
                continue
            text = pfad.read_text()
            with self.subTest(server=pfad.name):
                self.assertLess(text.index("gateAktivieren(server"), text.index("server.setRequestHandler("))
