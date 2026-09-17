"""Issue #197 Teil 2: die .claude/settings.json, die den PreToolUse-Hook fuer
Claude-Code-Agenten registriert.

Nur die reine Konfiguration wird hier geprueft — dass sie tatsaechlich in den
Container geschrieben wird (an allen drei Lebenszyklus-Stellen: anlegen,
update_agent, refresh_instructions), steht als Kommentar direkt neben jedem
Aufruf in agent_manager.py; ein Docker-gestuetzter Integrationstest dafuer
existiert fuer keine der bestehenden CLAUDE.md-Schreibstellen und waere hier
kein anderer Massstab.
"""
import json
import unittest

from app.core.agent_manager import (
    _CLAUDE_PRETOOLUSE_SETTINGS_JSON,
    claude_pretooluse_settings_path,
)


class ClaudePreToolUseSettingsTests(unittest.TestCase):
    def test_the_path_is_project_level_not_home(self):
        # Claude Code startet mit cwd=/workspace (agent_runner.py) und sucht
        # Projekt-Settings dort zuerst -- ~/.claude liegt in einem ANDEREN,
        # separaten Volume (Sitzungs-Volume), das bei bereits existierenden
        # Agenten schon belegt ist und ein neues Image-COPY dorthin ignoriert.
        self.assertEqual(claude_pretooluse_settings_path(), "/workspace/.claude/settings.json")

    def test_the_settings_are_valid_json(self):
        json.loads(_CLAUDE_PRETOOLUSE_SETTINGS_JSON)  # must not raise

    def test_the_hook_is_registered_for_every_tool(self):
        cfg = json.loads(_CLAUDE_PRETOOLUSE_SETTINGS_JSON)
        matchers = [h["matcher"] for h in cfg["hooks"]["PreToolUse"]]
        self.assertIn("*", matchers)

    def test_the_hook_points_at_the_agents_own_local_health_server(self):
        # Muss lokal bleiben (localhost:8080, derselbe Container) -- kein
        # Netzwerksprung nach aussen fuer eine Pruefung, die bei jedem
        # Werkzeugaufruf feuert.
        cfg = json.loads(_CLAUDE_PRETOOLUSE_SETTINGS_JSON)
        hook = cfg["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(hook["type"], "http")
        self.assertEqual(hook["url"], "http://localhost:8080/hooks/pretooluse")

    def test_the_hook_has_a_short_timeout(self):
        # Rein lokal -- sollte nie lange dauern; ein kurzes Timeout verhindert
        # ein haengendes Werkzeug, falls doch einmal etwas schiefgeht (faellt
        # dann per Claude-Code-eigenem Verhalten auf "nicht blockierend"
        # zurueck, nicht auf einen haengenden Zug).
        cfg = json.loads(_CLAUDE_PRETOOLUSE_SETTINGS_JSON)
        hook = cfg["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertLessEqual(hook["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
