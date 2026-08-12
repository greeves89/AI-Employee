"""Jeder Nutzer arbeitet mit seinem eigenen Claude- oder Codex-Abo.

Bis hierher kam der Abo-Zugang aus **einer** Einstellung für die ganze
Installation, und pflegen konnte sie nur ein Administrator. Wer die Plattform
nutzte, arbeitete zwangsläufig auf fremde Rechnung — oder gar nicht.

Die Grundlage lag seit dem Vormittag im Baum, war aber **wirkungslos**:
``agent_credentials.resolve()`` wurde von niemandem aufgerufen. Die Tabelle wurde
angelegt, der Auflöser existierte, und dann passierte nichts. Genau deshalb prüft
dieser Test zuerst die **Verdrahtung** und erst danach die Logik.

Der zweite Grund ist betrieblicher Natur: alle Codex-Agenten teilten sich einen
rotierenden Refresh-Token. Erneuert ihn einer, sind die anderen tot
(``refresh_token_reused``) — deshalb muss das Neuerstellen bis heute serialisiert
werden. Getrennte Zugänge sind getrennte Token-Familien.
"""

import unittest
from types import SimpleNamespace

from app.core import agent_credentials as creds


class HarnessDetectionTests(unittest.TestCase):
    """Ein „claude_code"-Agent auf dem Codex-Anbieter IST ein Codex-Agent —
    sonst bekäme er den falschen Zugang untergeschoben."""

    def test_codex_provider_wins_over_the_mode(self):
        self.assertEqual(creds.harness_of("claude_code", "codex"), "codex")

    def test_claude_code_is_the_default_mode(self):
        self.assertEqual(creds.harness_of(None, "anthropic"), "claude_code")

    def test_custom_llm_needs_no_subscription(self):
        """Dort haengt der Zugang am AI-Konto, nicht an einem Abo."""
        self.assertIsNone(creds.harness_of("custom_llm", "anthropic"))


class EnvForTests(unittest.TestCase):
    def test_an_oauth_token_and_an_api_key_go_into_different_variables(self):
        """Sie sehen unterschiedlich aus, und die CLI waehlt danach."""
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", creds.env_for("claude_code", "sk-ant-oat01-x"))
        self.assertIn("ANTHROPIC_API_KEY", creds.env_for("claude_code", "sk-ant-api03-x"))

    def test_codex_gets_the_auth_json(self):
        self.assertEqual(creds.env_for("codex", '{"a":1}'), {"CODEX_AUTH_JSON": '{"a":1}'})


class ResolveOrderTests(unittest.IsolatedAsyncioTestCase):
    """Eigener Zugang → Teamlizenz → nichts."""

    async def _resolve(self, *, personal, team, team_allowed, mode="claude_code"):
        orig_personal = creds.personal_credential
        orig_team = creds.team_secret
        orig_allowed = creds.team_license_allowed

        async def _p(_db, _uid, _h):
            return personal

        async def _t(_db, _h):
            return team

        creds.personal_credential = _p
        creds.team_secret = _t
        creds.team_license_allowed = lambda: team_allowed
        try:
            return await creds.resolve(None, owner_id="u1", mode=mode,
                                       model_provider="anthropic")
        finally:
            creds.personal_credential = orig_personal
            creds.team_secret = orig_team
            creds.team_license_allowed = orig_allowed

    async def test_the_own_credential_wins(self):
        source, harness, secret = await self._resolve(
            personal="meins", team="team", team_allowed=True)
        self.assertEqual((source, harness, secret), (creds.SOURCE_PERSONAL, "claude_code", "meins"))

    async def test_without_an_own_one_the_team_license_applies(self):
        source, _, secret = await self._resolve(
            personal=None, team="team", team_allowed=True)
        self.assertEqual((source, secret), (creds.SOURCE_TEAM, "team"))

    async def test_a_forbidden_team_license_is_not_used(self):
        """Der Schalter ist der Punkt, an dem eine Firma entscheidet: unser Konto
        ist fuer alle da, oder jeder bringt sein eigenes mit."""
        source, _, secret = await self._resolve(
            personal=None, team="team", team_allowed=False)
        self.assertEqual((source, secret), (creds.SOURCE_NONE, None))

    async def test_no_credential_at_all_is_reported_honestly(self):
        source, _, secret = await self._resolve(
            personal=None, team=None, team_allowed=True)
        self.assertEqual((source, secret), (creds.SOURCE_NONE, None))


class TheResolverIsActuallyWiredTests(unittest.IsolatedAsyncioTestCase):
    """Der eigentliche Punkt. Ein Aufloeser, den niemand ruft, aendert nichts —
    und genau so lag er einen halben Tag im Baum."""

    async def test_the_owner_credential_overrides_the_global_setting(self):
        from app.core.agent_manager import AgentManager

        mgr = AgentManager.__new__(AgentManager)
        mgr.db = None

        async def _fake_resolve(_db, *, owner_id, mode, model_provider):
            return creds.SOURCE_PERSONAL, "claude_code", "sk-ant-oat01-meins"

        orig = creds.resolve
        creds.resolve = _fake_resolve
        try:
            env = await mgr._owner_credential_env("u1", "claude_code", "anthropic")
        finally:
            creds.resolve = orig
        self.assertEqual(env, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-meins"})

    async def test_a_broken_lookup_does_not_stop_the_agent(self):
        """Ein Fehler beim Aufloesen darf keinen Agenten am Start hindern —
        dann greift wie bisher die globale Einstellung."""
        from app.core.agent_manager import AgentManager

        mgr = AgentManager.__new__(AgentManager)
        mgr.db = None

        async def _boom(*_a, **_kw):
            raise RuntimeError("DB weg")

        orig = creds.resolve
        creds.resolve = _boom
        try:
            self.assertEqual(await mgr._owner_credential_env("u1", "claude_code", "anthropic"), {})
        finally:
            creds.resolve = orig

    async def test_custom_llm_gets_nothing_injected(self):
        from app.core.agent_manager import AgentManager

        mgr = AgentManager.__new__(AgentManager)
        mgr.db = None
        async def _none(_db, *, owner_id, mode, model_provider):
            return creds.SOURCE_NONE, None, None

        orig = creds.resolve
        creds.resolve = _none
        try:
            self.assertEqual(await mgr._owner_credential_env("u1", "custom_llm", "openai"), {})
        finally:
            creds.resolve = orig

    def test_all_three_container_paths_use_it(self):
        """Anlegen, Neustart und Aktualisieren — fehlt einer, bekommt der Agent
        beim naechsten Neustart wieder den fremden Zugang."""
        import inspect

        from app.core import agent_manager

        src = inspect.getsource(agent_manager)
        self.assertEqual(src.count("await self._owner_credential_env("), 3)


class TheApiIsMineOnlyTests(unittest.TestCase):
    """Ein Abo-Token ist der Zugang zu einem bezahlten Konto einer anderen
    Person. Wer es einsehen koennte, koennte es benutzen."""

    def test_the_routes_are_scoped_to_me(self):
        from app.api.my_ai_credentials import router

        self.assertEqual(router.prefix, "/me/ai-credentials")

    def test_the_secret_is_never_returned(self):
        from app.api.my_ai_credentials import _to_response

        row = SimpleNamespace(harness="codex", label="privat", last_status="ok",
                              last_used_at=None, created_at=None,
                              secret_encrypted="GEHEIM")
        self.assertNotIn("GEHEIM", str(_to_response(row)))
        self.assertNotIn("secret_encrypted", _to_response(row))


if __name__ == "__main__":
    unittest.main()
