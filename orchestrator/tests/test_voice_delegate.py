"""Weiterreich-Modus der Echtzeit-Sprachfront (app/core/voice_delegate.py).

Anlass 23.09.2026: Im Echtzeit-Gespraech antwortete nicht der Agent, sondern Nova
Sonic mit eigener Werkzeugliste — der Triathlon-Coach behauptete am Telefon, er
habe keinen Garmin-Zugriff, waehrend derselbe Agent im Textchat die Schlafdaten
sofort lieferte. Gewuenscht: Nova nur als Ohr und Mund, jede Frage an den Agenten.

Die Tests halten fest, dass (a) der Agent-Wert die Plattform-Vorgabe schlaegt,
(b) im Modus nur Gespraechsfuehrung uebrig bleibt und ``ask_agent`` die
"immer benutzen"-Fassung ist, (c) die Werkzeugnamen zur echten Sprachsitzung
passen und (d) die Einstellung an allen Stellen durch den PATCH-Pfad kommt.
"""

import json
import pathlib
import unittest

from app.core import voice_delegate as vd

ORCH = pathlib.Path(__file__).resolve().parents[1]


def _spec(name):
    return {"toolSpec": {"name": name, "description": name, "inputSchema": {"json": "{}"}}}


def _names(tools):
    return [t["toolSpec"]["name"] for t in tools]


class ResolveTests(unittest.TestCase):
    def test_agent_value_wins(self):
        self.assertTrue(vd.resolve({vd.CONFIG_KEY: True}, "false"))
        self.assertFalse(vd.resolve({vd.CONFIG_KEY: False}, "true"))

    def test_platform_default_when_agent_has_none(self):
        self.assertTrue(vd.resolve({}, "true"))
        self.assertTrue(vd.resolve({vd.CONFIG_KEY: None}, "true"))
        self.assertFalse(vd.resolve({}, "false"))
        self.assertFalse(vd.resolve(None, None))  # nichts gesetzt: bisheriges Verhalten

    def test_string_values_from_the_settings_store(self):
        # Der Einstellungsspeicher kennt nur Strings; der PATCH-Pfad schreibt str(value).
        self.assertTrue(vd.resolve({}, "True"))
        self.assertTrue(vd.resolve({vd.CONFIG_KEY: "true"}, None))
        self.assertFalse(vd.resolve({}, ""))
        self.assertFalse(vd.resolve({}, "quatsch"))


class FilterToolsTests(unittest.TestCase):
    def test_only_conversation_tools_remain(self):
        tools = [_spec(n) for n in (
            "get_agent_status", "m365_calendar_today", "search_brain", "ask_agent",
            "cancel_task", "garmin_sleep", "mcp_call_tool", "refine_task",
        )]
        result = vd.filter_tools(tools)
        self.assertEqual(_names(result), ["ask_agent", "cancel_task", "refine_task"])

    def test_ask_agent_is_the_always_use_variant(self):
        # Die normale Beschreibung verbietet Status- und Wissensfragen — genau
        # das Gegenteil dessen, was hier gewollt ist.
        result = vd.filter_tools([_spec("ask_agent")])
        self.assertIs(result[0], vd.ASK_AGENT_DELEGATE_TOOL)
        self.assertIn("IMMER", result[0]["toolSpec"]["description"])
        schema = json.loads(result[0]["toolSpec"]["inputSchema"]["json"])
        self.assertEqual(schema["required"], ["instruction"])

    def test_ask_agent_is_added_if_missing(self):
        self.assertEqual(_names(vd.filter_tools([_spec("cancel_task")])), ["ask_agent", "cancel_task"])

    def test_names_match_the_real_voice_session(self):
        """Wird ein Werkzeug in der Sprachsitzung umbenannt, faellt es hier auf,
        statt im Weiterreich-Modus still zu verschwinden."""
        from app.services import realtime_voice_session as rvs

        real = {
            (getattr(rvs, n).get("toolSpec") or {}).get("name")
            for n in dir(rvs)
            if n.endswith("_TOOL") and isinstance(getattr(rvs, n), dict)
        }
        self.assertTrue(vd.KEEP_TOOLS <= real, sorted(vd.KEEP_TOOLS - real))


class PromptTests(unittest.TestCase):
    def test_prompt_demands_delegation_and_faithful_reading(self):
        p = vd.system_prompt("Mr. Triathlon", "Triathlon-Coach", "de")
        self.assertIn("ask_agent", p)
        self.assertIn("UNVERAENDERT", p)
        self.assertIn("Mr. Triathlon", p)
        self.assertIn("Triathlon-Coach", p)

    def test_prompt_has_no_tool_choice_for_missing_tools(self):
        # Der normale Prompt schickt Kalenderfragen an m365_calendar_today — das
        # Werkzeug gibt es hier nicht, und die Anweisung wuerde das Weiterreichen
        # untergraben.
        p = vd.system_prompt("X", "", "de")
        for fremd in ("m365_calendar_today", "search_brain", "get_agent_status", "mcp_call_tool"):
            self.assertNotIn(fremd, p)


class SettingPlumbingTests(unittest.TestCase):
    """Die erlaubten Schluessel stehen an mehreren Stellen (siehe
    test_nova_voice_setting.py) — fehlt einer, wird der Wert still verworfen."""

    def test_setting_is_writable(self):
        from app.services.settings_service import ALLOWED_KEYS
        self.assertIn(vd.SETTING_KEY, ALLOWED_KEYS)

    def test_setting_survives_the_patch_path(self):
        src = (ORCH / "app/api/settings.py").read_text(encoding="utf-8")
        block = src[src.index("_VOICE_FIELDS = ["):]
        block = block[: block.index("]")]
        self.assertIn(f'"{vd.SETTING_KEY}"', block)

    def test_schemas_know_the_field(self):
        from app.schemas.settings import SettingsUpdate, VoiceSettings
        from app.schemas.agent import AgentResponse

        self.assertIn(vd.SETTING_KEY, SettingsUpdate.model_fields)  # sonst kommt der PATCH nie an
        self.assertIn(vd.SETTING_KEY, VoiceSettings.model_fields)
        self.assertIn(vd.CONFIG_KEY, AgentResponse.model_fields)

    def test_voice_session_uses_the_module(self):
        src = (ORCH / "app/services/realtime_voice_session.py").read_text(encoding="utf-8")
        self.assertIn("_vd.resolve(cfg, await svc.get(_vd.SETTING_KEY))", src)
        self.assertIn("_vd.filter_tools(_tools)", src)
        self.assertIn("if not self._delegate_all:", src)
        self.assertIn("_vd.system_prompt(agent_name, agent_role, language)", src)


if __name__ == "__main__":
    unittest.main()
