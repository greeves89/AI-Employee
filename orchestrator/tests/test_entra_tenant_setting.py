"""Die Verzeichnis-ID (Mandant) muss über die Oberfläche setzbar sein.

Vorfall 2026-08-05: Nach dem Anlegen einer App-Registrierung schlug der Login mit
`AADSTS50194` fehl — die App ist Single-Tenant, die Anmeldung lief aber über den
`/common`-Endpunkt. Der Code kann das längst (`apply_tenant()` setzt die ID in die
Anmelde- und Token-URL ein und wird vom SSO-Login aufgerufen); es gab nur kein
Feld, um sie einzutragen: In der Oberfläche standen nur Client-ID und Secret.

Dieselbe Doppel-Falle wie bei `nova_sonic_voice`: Die erlaubten Schlüssel stehen
an ZWEI Stellen, und beide müssen den Namen kennen — sonst wird der Wert beim
Speichern still verworfen und der Nutzer sieht „Gespeichert." ohne Wirkung.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "frontend/src/app/settings/view.tsx"


class EntraTenantSettingTests(unittest.TestCase):
    def test_key_is_allowed_in_the_service(self):
        from app.services.settings_service import ALLOWED_KEYS
        self.assertIn("oauth_microsoft_tenant_id", ALLOWED_KEYS)

    def test_request_schema_accepts_it(self):
        from app.schemas.settings import SettingsUpdate
        self.assertIn("oauth_microsoft_tenant_id", SettingsUpdate.model_fields)

    def test_patch_endpoint_maps_it(self):
        """Ohne Eintrag im Mapping verwirft der Endpunkt den Wert stillschweigend."""
        api = (ROOT / "orchestrator/app/api/settings.py").read_text()
        self.assertIn('"oauth_microsoft_tenant_id": "oauth_microsoft_tenant_id"', api)

    def test_response_returns_it(self):
        """Sonst kann die Oberfläche den aktuellen Stand nicht anzeigen."""
        from app.schemas.settings import SettingsResponse
        self.assertIn("oauth_microsoft_tenant_id", SettingsResponse.model_fields)

    def test_ui_has_the_field(self):
        text = UI.read_text()
        self.assertIn("microsoftTenantId", text)
        self.assertIn("data.oauth_microsoft_tenant_id", text)

    def test_ui_explains_why_it_matters(self):
        """Ohne Hinweis rät der Admin, woher die ID kommt — der Fehlercode hilft."""
        self.assertIn("AADSTS50194", UI.read_text())

    def test_apply_tenant_rewrites_the_authority(self):
        """Der eigentliche Zweck: /common gegen die Mandanten-ID tauschen."""
        from app.config import settings
        from app.core.oauth_providers import apply_tenant
        old = settings.oauth_microsoft_tenant_id
        try:
            settings.oauth_microsoft_tenant_id = "9877ebf8-ac3a-4761-acc8-137423a40358"
            url = apply_tenant("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
            self.assertIn("9877ebf8-ac3a-4761-acc8-137423a40358", url)
            self.assertNotIn("/common/", url)
            settings.oauth_microsoft_tenant_id = "common"
            self.assertIn("/common/", apply_tenant(
                "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"))
        finally:
            settings.oauth_microsoft_tenant_id = old


if __name__ == "__main__":
    unittest.main()


class MicrosoftScopeSelectionTests(unittest.TestCase):
    """Die angeforderten Rechte müssen zur App-Registrierung passen.

    Vorfall 2026-08-05: Nach dem Login kam „Genehmigung erforderlich" — der Server
    forderte 17 Graph-Rechte an, in der Registrierung standen acht. Entra verlangt
    dann eine Administrator-Genehmigung und der Login bleibt hängen. Der Admin muss
    die Auswahl also an seine Registrierung anpassen können.
    """

    def test_required_scopes_are_never_droppable(self):
        """Ohne openid/email/profile keine Anmeldung, ohne offline_access kein
        Aktualisierungs-Token — die Verbindung bräche nach einer Stunde ab."""
        from app.core.oauth_providers import MICROSOFT_REQUIRED_SCOPES
        for s in ("openid", "email", "profile", "offline_access"):
            self.assertIn(s, MICROSOFT_REQUIRED_SCOPES)

    def test_empty_selection_means_everything(self):
        """Bisheriges Verhalten bleibt, solange niemand etwas abwählt."""
        from app.config import settings
        from app.core.oauth_providers import (
            MICROSOFT_OPTIONAL_SCOPES, MICROSOFT_REQUIRED_SCOPES, microsoft_scopes,
        )
        old = getattr(settings, "oauth_microsoft_scopes", "")
        try:
            settings.oauth_microsoft_scopes = ""
            self.assertEqual(
                set(microsoft_scopes()),
                set(MICROSOFT_REQUIRED_SCOPES) | set(MICROSOFT_OPTIONAL_SCOPES),
            )
        finally:
            settings.oauth_microsoft_scopes = old

    def test_narrow_selection_is_honoured(self):
        from app.config import settings
        from app.core.oauth_providers import microsoft_scopes
        old = getattr(settings, "oauth_microsoft_scopes", "")
        try:
            settings.oauth_microsoft_scopes = "Mail.ReadWrite,ChannelMessage.Send"
            got = microsoft_scopes()
            self.assertIn("Mail.ReadWrite", got)
            self.assertIn("ChannelMessage.Send", got)
            self.assertNotIn("Calendars.ReadWrite", got)
            self.assertIn("offline_access", got)  # Pflicht bleibt
        finally:
            settings.oauth_microsoft_scopes = old

    def test_unknown_scope_is_not_passed_through(self):
        """Ein Tippfehler darf nicht als Recht an Entra gehen."""
        from app.config import settings
        from app.core.oauth_providers import microsoft_scopes
        old = getattr(settings, "oauth_microsoft_scopes", "")
        try:
            settings.oauth_microsoft_scopes = "Mail.ReadWrite,Dir.ReadAll.Everything"
            self.assertNotIn("Dir.ReadAll.Everything", microsoft_scopes())
        finally:
            settings.oauth_microsoft_scopes = old

    def test_login_uses_the_selection(self):
        """Der Anmelde-Weg nahm bisher die feste Liste — genau das war der Fehler."""
        import inspect
        from app.services import sso_service
        src = inspect.getsource(sso_service)
        self.assertIn("_scopes_for(provider)", src)
        self.assertIn("microsoft_scopes", src)

    def test_setting_is_writable_and_returned(self):
        from app.schemas.settings import SettingsResponse, SettingsUpdate
        from app.services.settings_service import ALLOWED_KEYS
        self.assertIn("oauth_microsoft_scopes", ALLOWED_KEYS)
        self.assertIn("oauth_microsoft_scopes", SettingsUpdate.model_fields)
        self.assertIn("microsoft_optional_scopes", SettingsResponse.model_fields)
        api = (ROOT / "orchestrator/app/api/settings.py").read_text()
        self.assertIn('"oauth_microsoft_scopes": "oauth_microsoft_scopes"', api)

    def test_ui_offers_the_picker(self):
        text = UI.read_text()
        self.assertIn("microsoft_optional_scopes", text)
        self.assertIn("data.oauth_microsoft_scopes", text)
