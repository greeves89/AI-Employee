"""Agent mit Stimme im Teams-Termin — Einrichtung und Rueckruf.

Microsoft bietet zwei Medien-Modi. Application-hosted media (roher Audiostrom) braucht
ein .NET-Medienmodul, offene Medienports und Calls.AccessMedia.All. Service-hosted
media kommt mit HTTPS und einem Webhook aus — der Bot spricht ueber playPrompt und
hoert ueber recordResponse. Dieses Modul geht bewusst den zweiten Weg.

Fuer den Administrator soll genau EIN Schritt bleiben: eine Adresse in Azure eintragen.
"""

import unittest
from pathlib import Path

from app.core import teams_calling as tc

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"


class CallbackUrlTests(unittest.TestCase):
    def test_url_is_stable_and_absolute(self):
        self.assertEqual(
            tc.callback_url("https://agents.example.de"),
            "https://agents.example.de/api/v1/teams/calling/callback",
        )

    def test_trailing_slash_does_not_double(self):
        self.assertEqual(
            tc.callback_url("https://agents.example.de/"),
            tc.callback_url("https://agents.example.de"),
        )

    def test_https_is_checked_upfront(self):
        """Microsoft ruft nur HTTPS zurueck. Unter http bleibt der Agent stumm,
        ohne dass hier ein Fehler auftaucht — das gehoert VOR die Einrichtung."""
        self.assertTrue(tc.public_base_is_https("https://a.de"))
        self.assertFalse(tc.public_base_is_https("http://a.de"))
        self.assertFalse(tc.public_base_is_https(""))


class ConfigTests(unittest.TestCase):
    FULL = {tc.APP_ID: "app", tc.APP_SECRET: "geheim", tc.TENANT_ID: "tenant"}

    def test_all_three_are_required(self):
        self.assertTrue(tc.is_configured(self.FULL))
        for missing in (tc.APP_ID, tc.APP_SECRET, tc.TENANT_ID):
            with self.subTest(missing=missing):
                cfg = {**self.FULL, missing: ""}
                self.assertFalse(tc.is_configured(cfg))

    def test_enabled_needs_configured(self):
        """Eingeschaltet ohne Angaben waere ein Knopf, der sicher scheitert."""
        self.assertFalse(tc.is_enabled({tc.ENABLED: "true"}))
        self.assertTrue(tc.is_enabled({**self.FULL, tc.ENABLED: "true"}))

    def test_enabled_is_off_by_default(self):
        self.assertFalse(tc.is_enabled(self.FULL))


class PermissionTests(unittest.TestCase):
    NAMES = {p[0] for p in tc.REQUIRED_PERMISSIONS}

    def test_the_four_needed_ones(self):
        self.assertEqual(self.NAMES, {
            "Calls.JoinGroupCall.All",
            "Calls.JoinGroupCallAsGuest.All",
            "Calls.InitiateGroupCall.All",
            "OnlineMeetings.Read.All",
        })

    def test_raw_media_permission_is_NOT_requested(self):
        """Calls.AccessMedia.All erlaubt den Zugriff auf den rohen Audiostrom ALLER
        Teilnehmer. Dieser Weg nutzt sie nicht — ein Recht, das man nicht braucht,
        fordert man beim Kunden auch nicht an."""
        # Nicht in der Liste, die dem Administrator zur Freigabe vorgelegt wird.
        self.assertNotIn("Calls.AccessMedia.All", self.NAMES)
        # Und die Auslassung ist begruendet, nicht vergessen — sonst traegt sie
        # beim naechsten Umbau jemand "der Vollstaendigkeit halber" nach.
        self.assertIn("Bewusst NICHT dabei", (ORCH / "app/core/teams_calling.py").read_text())

    def test_every_permission_says_why(self):
        for name, why in tc.REQUIRED_PERMISSIONS:
            with self.subTest(permission=name):
                self.assertTrue(why.strip())


class MediaModeTests(unittest.TestCase):
    SRC = ORCH / "app/core/teams_calling.py"

    def test_service_hosted_media_is_used(self):
        """Der Kern des Entwurfs: Microsoft haelt die Medien, deshalb weder .NET
        noch offene Medienports."""
        src = self.SRC.read_text()
        self.assertIn("serviceHostedMediaConfig", src)
        self.assertNotIn("appHostedMediaConfig", src)

    def test_speak_and_listen_exist(self):
        src = self.SRC.read_text()
        self.assertIn("playPrompt", src)
        self.assertIn("recordResponse", src)

    def test_hang_up_exists(self):
        """Ohne Auflegen bleibt der Bot bis zum Ende im Termin."""
        self.assertIn("async def hang_up", self.SRC.read_text())


class CallbackEndpointTests(unittest.TestCase):
    SRC = ORCH / "app/api/teams_calling.py"

    def test_validation_token_is_echoed_as_plain_text(self):
        """Microsoft schickt beim Eintragen ein validationToken und erwartet es
        unveraendert als reinen Text. Mit JSON gilt die Adresse als ungueltig."""
        src = self.SRC.read_text()
        self.assertIn("validationToken", src)
        self.assertIn("PlainTextResponse", src)

    def test_never_returns_5xx_to_microsoft(self):
        """Sonst wiederholt Microsoft und schaltet die Adresse ab."""
        block = self.SRC.read_text().split("async def calling_callback")[1]
        self.assertIn("except Exception", block)
        self.assertIn('"status": "accepted"', block)

    def test_setup_and_test_are_admin_only(self):
        src = self.SRC.read_text()
        self.assertEqual(src.count("Depends(require_admin)"), 3)

    def test_callback_itself_is_public(self):
        """Microsoft kann sich nicht anmelden — der Rueckruf MUSS offen sein."""
        block = self.SRC.read_text().split("async def calling_callback")[1].split("async def ")[0]
        self.assertNotIn("require_admin", block)
        self.assertNotIn("require_auth", block)


class SettingsPathTests(unittest.TestCase):
    FIELDS = ("teams_calling_app_id", "teams_calling_app_secret",
              "teams_calling_tenant_id", "teams_calling_enabled")

    def test_allowed_keys(self):
        src = (ORCH / "app/services/settings_service.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)

    def test_secret_is_encrypted(self):
        from app.services.settings_service import SECRET_KEYS
        self.assertIn("teams_calling_app_secret", SECRET_KEYS)

    def test_request_schema(self):
        src = (ORCH / "app/schemas/settings.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f"{f}:", src)

    def test_patch_mapping(self):
        src = (ORCH / "app/api/settings.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)

    def test_returned_to_the_ui(self):
        """Ueber /teams/calling/setup — die Karte braucht die Adresse und den Zustand."""
        src = (ORCH / "app/api/teams_calling.py").read_text()
        self.assertIn('"callback_url"', src)
        self.assertIn('"configured"', src)


class AdminUiTests(unittest.TestCase):
    CARD = REPO / "frontend/src/components/settings/teams-calling-config.tsx"

    def test_card_exists_and_is_admin_only(self):
        src = (REPO / "frontend/src/app/settings/view.tsx").read_text()
        self.assertIn("TeamsCallingConfig", src)
        block = src.split("<TeamsCallingConfig />")[0][-200:]
        self.assertIn("isAdmin", block)

    def test_the_url_is_copyable_and_first(self):
        """Das ist der eine Schritt, den der Administrator wirklich tun muss."""
        src = self.CARD.read_text()
        self.assertIn("Schritt 1", src)
        self.assertIn("clipboard.writeText", src)
        self.assertLess(src.index("Schritt 1"), src.index("Schritt 2"))

    def test_https_warning_comes_before_the_fields(self):
        src = self.CARD.read_text()
        self.assertLess(src.index("https_ok"), src.index("Schritt 2"))

    def test_empty_secret_does_not_wipe_the_stored_one(self):
        """Sonst loescht ein Speichern ohne erneutes Eintippen das Geheimnis, und
        der Agent bleibt Terminen fern."""
        src = self.CARD.read_text()
        self.assertIn("secret.trim() ?", src)

    def test_guide_is_linked(self):
        self.assertIn("TEAMS_CALLING_SETUP.md", self.CARD.read_text())

    def test_limitation_is_stated_not_hidden(self):
        src = self.CARD.read_text()
        self.assertIn("abwechselnd", src)
        self.assertIn("Calls.AccessMedia.All", src)


class GuideTests(unittest.TestCase):
    DOC = REPO / "docs/TEAMS_CALLING_SETUP.md"

    def test_guide_exists(self):
        self.assertTrue(self.DOC.exists())

    def test_covers_every_step(self):
        src = self.DOC.read_text()
        for step in ("App-Registrierung", "Geheimnis", "Berechtigungen",
                     "Azure Bot", "Zustimmung", "Prüfen"):
            with self.subTest(step=step):
                self.assertIn(step, src)

    def test_names_the_common_failure(self):
        """Fehlende Administratorzustimmung ist der haeufigste Grund."""
        src = self.DOC.read_text()
        self.assertIn("Zustimmung", src)
        self.assertIn("Lobby", src)

    def test_warns_about_the_one_time_secret(self):
        self.assertIn("nur EINMAL sichtbar", self.DOC.read_text())

    def test_states_what_does_not_work(self):
        src = self.DOC.read_text()
        self.assertIn("durchgehend mit", src.lower().replace("hört", "h"))


if __name__ == "__main__":
    unittest.main()
