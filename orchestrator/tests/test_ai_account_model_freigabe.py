"""Modell-Freigabe je AI-Account (Kundenwunsch 2026-08-18).

Der Administrator gibt Modelle eines AI-Accounts gezielt frei; nur
freigegebene sind bei der Agenten-Erstellung und beim Umverbinden waehlbar.
Bestandsdaten ohne ``enabled``-Feld gelten als freigegeben — nichts bricht.
"""

import unittest

from app.api.ai_accounts import AIModelEntry, _normalize_models, enabled_model_names


class EnabledModelNamesTests(unittest.TestCase):
    def test_legacy_entries_without_flag_stay_usable(self):
        models = [{"name": "gpt-5.6-sol"}, "altes-string-modell"]
        self.assertEqual(enabled_model_names(models), ["gpt-5.6-sol", "altes-string-modell"])

    def test_only_an_explicit_false_locks_a_model(self):
        models = [
            {"name": "frei", "enabled": True},
            {"name": "gesperrt", "enabled": False},
            {"name": "bestand"},
        ]
        self.assertEqual(enabled_model_names(models), ["frei", "bestand"])

    def test_empty_and_nameless_entries_are_skipped(self):
        self.assertEqual(enabled_model_names([{"enabled": True}, {}, None if False else {"name": ""}]), [])
        self.assertEqual(enabled_model_names([]), [])
        self.assertEqual(enabled_model_names(None), [])


class NormalizeCarriesTheFlagTests(unittest.TestCase):
    def test_normalize_keeps_enabled_and_defaults_to_true(self):
        out = _normalize_models(
            [{"name": "a", "enabled": False}, {"name": "b"}, "c"],
            default_provider="azure-openai", default_endpoint="https://e",
        )
        by_name = {m["name"]: m for m in out}
        self.assertFalse(by_name["a"]["enabled"])
        self.assertTrue(by_name["b"]["enabled"])
        # Legacy-Strings haben kein Flag — enabled_model_names behandelt sie als frei.
        self.assertEqual(by_name["c"]["provider_type"], "azure-openai")

    def test_schema_accepts_and_defaults_the_flag(self):
        self.assertTrue(AIModelEntry(name="x", provider_type="azure-openai").enabled)
        self.assertFalse(AIModelEntry(name="x", provider_type="azure-openai", enabled=False).enabled)


class GatesAreWiredTests(unittest.TestCase):
    """Beide Bindungswege (Erstellung + Umverbinden) muessen die Freigabe pruefen."""

    def test_create_agent_checks_enabled_models(self):
        import inspect
        from app.api import agents
        src = inspect.getsource(agents.create_agent)
        self.assertIn("enabled_model_names(account.models)", src)
        self.assertIn("keine freigegebenen Modelle", src)

    def test_rebind_endpoint_checks_enabled_models(self):
        import inspect
        from app.api import agents
        src = inspect.getsource(agents.set_agent_ai_account) if hasattr(agents, "set_agent_ai_account") else ""
        if not src:
            # Endpunktname robust finden
            import re
            module_src = inspect.getsource(agents)
            m = re.search(r"@router\.patch\(\"/\{agent_id\}/ai-account\"\)\s+async def (\w+)", module_src)
            assert m, "ai-account-Endpunkt nicht gefunden"
            src = inspect.getsource(getattr(agents, m.group(1)))
        self.assertIn("enabled_model_names(account.models)", src)
        self.assertIn("nicht freigegeben", src)


if __name__ == "__main__":
    unittest.main()
