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
