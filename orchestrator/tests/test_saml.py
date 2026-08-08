"""SAML 2.0 SSO + Zuordnung von IdP-Gruppen auf Rollen.

Die Signaturpruefung selbst wird BEWUSST nicht getestet, weil sie bewusst nicht selbst
geschrieben ist: XML-DSig von Hand zu pruefen ist der klassische Ort fuer
Signature-Wrapping (XSW), ein Kanonisierungsfehler dort ist ein Authentifizierungs-
Bypass. Das macht ``python3-saml`` auf ``xmlsec``.

Geprueft wird hier das Drumherum — und das ist genau das, was in diesem Projekt schon
mehrfach schiefgegangen ist: dass eine Einstellung nur an drei von vier Stellen steht,
dass ein zweiter Anmeldeweg an der Freigabepflicht vorbeigeht, dass eine
Gruppenzuordnung jemandem still Rechte wegnimmt.
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core import saml_config

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "orchestrator"

try:  # Wie beim Bedrock-Modul: fehlt die Systembibliothek, wird uebersprungen.
    import xmlsec  # noqa: F401
    from onelogin.saml2.auth import OneLogin_Saml2_Auth  # noqa: F401
    HAS_XMLSEC = True
except Exception:  # noqa: BLE001
    HAS_XMLSEC = False


class ConfiguredTests(unittest.TestCase):
    def test_needs_entity_sso_url_and_certificate(self):
        full = {
            saml_config.IDP_ENTITY_ID: "https://idp.example/meta",
            saml_config.IDP_SSO_URL: "https://idp.example/sso",
            saml_config.IDP_CERT: "MIIC...",
        }
        self.assertTrue(saml_config.is_configured(full))

    def test_without_certificate_it_is_not_offered(self):
        """Ohne Zertifikat waere keine Signatur pruefbar — dann darf der Knopf gar
        nicht erst erscheinen, statt beim Klick in einen Fehler zu laufen."""
        partial = {
            saml_config.IDP_ENTITY_ID: "https://idp.example/meta",
            saml_config.IDP_SSO_URL: "https://idp.example/sso",
            saml_config.IDP_CERT: "",
        }
        self.assertFalse(saml_config.is_configured(partial))

    def test_empty_config_is_not_configured(self):
        self.assertFalse(saml_config.is_configured({}))


class SettingsShapeTests(unittest.TestCase):
    def test_assertions_must_be_signed(self):
        """Der Kern des Verfahrens: eine unsignierte Assertion anzunehmen hiesse,
        dass jeder eine Antwort mit beliebiger E-Mail schicken koennte."""
        s = saml_config.build_saml_settings(
            {saml_config.IDP_ENTITY_ID: "x", saml_config.IDP_SSO_URL: "y",
             saml_config.IDP_CERT: "z"},
            "https://ai.example.de",
        )
        self.assertTrue(s["security"]["wantAssertionsSigned"])
        self.assertTrue(s["strict"], "Ohne strict werden Fehler zu Warnungen.")
        self.assertTrue(s["security"]["rejectUnsolicitedResponsesWithInResponseTo"])

    def test_urls_are_built_from_the_public_base(self):
        s = saml_config.build_saml_settings({}, "https://ai.example.de/")
        self.assertEqual(s["sp"]["assertionConsumerService"]["url"],
                         "https://ai.example.de/api/v1/auth/sso/saml/acs")

    def test_sp_entity_id_can_be_overridden(self):
        s = saml_config.build_saml_settings(
            {saml_config.SP_ENTITY_ID: "urn:eigene:id"}, "https://ai.example.de")
        self.assertEqual(s["sp"]["entityId"], "urn:eigene:id")


class IdentityTests(unittest.TestCase):
    def test_plain_attribute_names(self):
        email, name = saml_config.extract_identity(
            {"email": ["a@b.de"], "displayName": ["Anna B"]}, "")
        self.assertEqual((email, name), ("a@b.de", "Anna B"))

    def test_adfs_style_claim_urls(self):
        """ADFS und Entra ID liefern lange URN-Namen — eine einzige Annahme reicht nicht."""
        attrs = {
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["c@d.de"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": ["Carl D"],
        }
        self.assertEqual(saml_config.extract_identity(attrs, ""), ("c@d.de", "Carl D"))

    def test_nameid_is_the_fallback(self):
        email, name = saml_config.extract_identity({}, "e@f.de")
        self.assertEqual(email, "e@f.de")
        self.assertEqual(name, "e")

    def test_a_nameid_that_is_not_an_email_is_not_used_as_one(self):
        email, _ = saml_config.extract_identity({}, "S-1-5-21-1234")
        self.assertEqual(email, "")

    def test_string_instead_of_list(self):
        self.assertEqual(saml_config.extract_identity({"email": "g@h.de"}, "")[0], "g@h.de")


class GroupTests(unittest.TestCase):
    def test_default_attribute(self):
        self.assertEqual(saml_config.extract_groups({"groups": ["A", "B"]}, {}), ["A", "B"])

    def test_configured_attribute(self):
        cfg = {saml_config.GROUP_ATTRIBUTE: "memberOf"}
        self.assertEqual(saml_config.extract_groups({"memberOf": ["X"]}, cfg), ["X"])

    def test_missing_attribute_is_empty(self):
        self.assertEqual(saml_config.extract_groups({}, {}), [])


class RoleMappingTests(unittest.TestCase):
    MAP = {"IT-Admins": "admin", "Teamleitung": "manager", "Alle": "member"}

    def test_highest_role_wins(self):
        """Wer in mehreren Gruppen steht, darf nicht davon abhaengen, wie das
        Verzeichnis sie sortiert."""
        self.assertEqual(saml_config.role_for_groups(["Alle", "IT-Admins"], self.MAP), "admin")
        self.assertEqual(saml_config.role_for_groups(["IT-Admins", "Alle"], self.MAP), "admin")

    def test_case_insensitive(self):
        self.assertEqual(saml_config.role_for_groups(["it-admins"], self.MAP), "admin")

    def test_no_match_changes_nothing(self):
        self.assertIsNone(saml_config.role_for_groups(["Fremde"], self.MAP))

    def test_empty_map_changes_nothing(self):
        """Sonst wuerde eine leere Zuordnung jedem die Rechte nehmen."""
        self.assertIsNone(saml_config.role_for_groups(["IT-Admins"], {}))

    def test_unknown_role_name_is_ignored(self):
        self.assertIsNone(saml_config.role_for_groups(["X"], {"X": "gottkaiser"}))

    def test_broken_json_is_no_mapping_not_a_crash(self):
        self.assertEqual(saml_config.parse_group_role_map("{kaputt"), {})
        self.assertEqual(saml_config.parse_group_role_map(""), {})
        self.assertEqual(saml_config.parse_group_role_map('["liste"]'), {})


class ApplyGroupRoleTests(unittest.IsolatedAsyncioTestCase):
    """Die Rollenaenderung selbst — hier kann man echten Schaden anrichten."""

    def _service(self, admin_count=2):
        from app.services.sso_service import SSOService

        svc = SSOService.__new__(SSOService)
        svc.db = SimpleNamespace(commit=AsyncMock(), scalar=AsyncMock(return_value=admin_count))
        return svc

    async def _user(self, role):
        from app.models.user import UserRole
        return SimpleNamespace(email="a@b.de", role=role)

    async def test_promotes_to_admin(self):
        from app.models.user import UserRole
        user = await self._user(UserRole.MEMBER)
        changed = await self._service().apply_group_role(user, ["IT"], {"IT": "admin"})
        self.assertTrue(changed)
        self.assertEqual(user.role, UserRole.ADMIN)

    async def test_no_match_leaves_the_role_alone(self):
        from app.models.user import UserRole
        user = await self._user(UserRole.MANAGER)
        self.assertFalse(await self._service().apply_group_role(user, ["Fremde"], {"IT": "admin"}))
        self.assertEqual(user.role, UserRole.MANAGER)

    async def test_last_admin_is_not_demoted(self):
        """Sonst sperrt eine Gruppenzuordnung die Plattform aus."""
        from app.models.user import UserRole
        user = await self._user(UserRole.ADMIN)
        changed = await self._service(admin_count=1).apply_group_role(
            user, ["Alle"], {"Alle": "member"})
        self.assertFalse(changed)
        self.assertEqual(user.role, UserRole.ADMIN)

    async def test_demotion_works_when_other_admins_remain(self):
        from app.models.user import UserRole
        user = await self._user(UserRole.ADMIN)
        changed = await self._service(admin_count=3).apply_group_role(
            user, ["Alle"], {"Alle": "member"})
        self.assertTrue(changed)
        self.assertEqual(user.role, UserRole.MEMBER)


class NoSecondLoginPathTests(unittest.TestCase):
    """SAML darf kein zweiter Anmeldeweg neben OIDC sein."""

    AUTH = ORCH / "app/api/auth.py"

    def test_saml_uses_the_shared_user_resolution(self):
        src = self.AUTH.read_text()
        self.assertIn("_find_or_create_user", src,
                      "SAML legt Konten auf eigenem Weg an.")

    def test_saml_uses_the_shared_session_tail(self):
        """Dort haengt die Freigabepflicht. Ein Nachbau koennte sie vergessen."""
        src = self.AUTH.read_text()
        acs = src.split("async def saml_acs")[1]
        self.assertIn("finish_sso_login", acs)

    def test_the_shared_tail_still_checks_approval(self):
        src = self.AUTH.read_text()
        tail = src.split("def finish_sso_login")[1].split("\n# --- SAML")[0]
        self.assertIn("approved", tail)

    def test_attributes_are_only_read_after_authentication(self):
        """Ein Zugriff auf die Attribute VOR is_authenticated waere das Einfallstor."""
        src = self.AUTH.read_text()
        acs = src.split("async def saml_acs")[1]
        auth_check = acs.index("is_authenticated")
        attrs = acs.index("get_attributes")
        self.assertLess(auth_check, attrs,
                        "Attribute werden vor der Signaturpruefung gelesen.")

    def test_return_target_is_validated(self):
        """Sonst waere der Login-Endpunkt eine offene Weiterleitung."""
        src = self.AUTH.read_text()
        login = src.split("async def saml_login")[1].split("\n@router")[0]
        self.assertIn("safe_internal_path", login)

    def test_import_error_is_caught_separately_and_loud(self):
        """Fehlendes xmlsec ist ein Installationsfehler, kein Betriebszustand —
        genau die Falle, die die Vertretungskette totgelegt hat."""
        src = self.AUTH.read_text()
        block = src.split("async def _saml_auth")[1].split("\n@router")[0]
        self.assertIn("except ImportError", block)
        self.assertIn("logger.error", block)


class RouteOrderTests(unittest.TestCase):
    """Die SAML-Routen MUESSEN vor `/sso/{provider}/…` stehen.

    FastAPI ordnet in Deklarationsreihenfolge zu. Steht die allgemeine Route zuerst,
    landet `/sso/saml/login` bei der OIDC-Verarbeitung und scheitert mit „Unknown
    provider" — ohne dass irgendwo ein Fehler sichtbar waere ausser einem toten Knopf
    auf der Anmeldeseite. Genau das war beim ersten Einbau der Fall.
    """

    def _sso_paths(self):
        from app.api import auth as auth_api
        return [r.path for r in auth_api.router.routes if "/sso/" in r.path]

    def test_saml_routes_come_first(self):
        paths = self._sso_paths()
        generic = min(i for i, p in enumerate(paths) if "{provider}" in p)
        for name in ("/auth/sso/saml/login", "/auth/sso/saml/acs", "/auth/sso/saml/metadata"):
            with self.subTest(route=name):
                self.assertLess(paths.index(name), generic,
                                f"{name} wird von /sso/{{provider}}/… abgefangen.")

    def test_the_frontend_pattern_reaches_saml(self):
        """Die Anmeldeseite baut `/auth/sso/{name}/login` fuer JEDEN Anbieter aus der
        Liste — SAML muss ueber genau dieses Muster erreichbar sein."""
        src = (REPO / "frontend/src/app/login/page.tsx").read_text()
        self.assertIn("/api/v1/auth/sso/${provider}/login", src)
        self.assertIn("/auth/sso/saml/login", self._sso_paths())


class SettingsPathTests(unittest.TestCase):
    """Vier Stellen, sonst meldet die Oberflaeche „Gespeichert." und nichts passiert."""

    FIELDS = ("saml_idp_entity_id", "saml_idp_sso_url", "saml_idp_x509_cert",
              "saml_group_attribute", "saml_group_role_map")

    def test_allowed_keys(self):
        src = (ORCH / "app/services/settings_service.py").read_text()
        for f in self.FIELDS:
            with self.subTest(field=f):
                self.assertIn(f'"{f}"', src)

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
        src = (ORCH / "app/api/settings.py").read_text()
        self.assertIn("_saml_response_fields", src)
        self.assertIn("saml_configured", src)

    def test_secrets_are_admin_only(self):
        """Die Einrichtungsangaben sind interne Infrastruktur — wie bei Exchange."""
        src = (ORCH / "app/api/settings.py").read_text()
        block = src.split("async def _saml_response_fields")[1]
        self.assertIn("if admin else", block)


class AdminUiTests(unittest.TestCase):
    def test_setup_card_exists_and_is_admin_only(self):
        src = (REPO / "frontend/src/app/settings/view.tsx").read_text()
        self.assertIn("SamlConfig", src)
        block = src.split("<SamlConfig />")[0][-200:]
        self.assertIn("isAdmin", block, "Einrichtungsangaben sind interne Infrastruktur.")

    def test_metadata_link_is_the_first_step(self):
        """Der Ablauf beim Kunden faengt IMMER damit an, unsere Metadaten beim
        Anbieter einzutragen — das gehoert nach oben, nicht an den Rand."""
        src = (REPO / "frontend/src/components/settings/saml-config.tsx").read_text()
        self.assertIn("auth/sso/saml/metadata", src)
        self.assertIn("Schritt 1", src)

    def test_broken_role_map_is_caught_before_saving(self):
        src = (REPO / "frontend/src/components/settings/saml-config.tsx").read_text()
        self.assertIn("JSON.parse(roleMap)", src)


class PackagingTests(unittest.TestCase):
    def test_dependency_is_declared(self):
        self.assertIn("python3-saml", (ORCH / "pyproject.toml").read_text())

    def test_image_brings_the_system_library(self):
        src = (ORCH / "Dockerfile").read_text()
        self.assertIn("libxmlsec1", src)

    def test_image_avoids_the_libxml2_mismatch(self):
        """lxml und xmlsec gegen verschiedene libxml2 gebaut = Laufzeitfehler beim
        ERSTEN SAML-Aufruf, nicht beim Bauen. Genau das ist lokal passiert."""
        src = (ORCH / "Dockerfile").read_text()
        self.assertIn("--no-binary lxml,xmlsec", src)


@unittest.skipUnless(HAS_XMLSEC, "xmlsec/python3-saml lokal nicht ladbar (libxml2-Versionskonflikt)")
class LibraryTests(unittest.TestCase):
    """Laeuft nur dort, wo die Bibliothek geladen werden kann — im Container."""

    IDP = {
        saml_config.IDP_ENTITY_ID: "https://idp.example/metadata",
        saml_config.IDP_SSO_URL: "https://idp.example/sso",
        saml_config.IDP_CERT: "MIIBkTCB+wIJAJ...",
    }

    def test_settings_are_accepted_by_the_library(self):
        """Die selbst gebaute Konfiguration muss python3-saml auch wirklich passen —
        ein Tippfehler in einem Schluessel faellt sonst erst beim ersten Anmelden auf."""
        from onelogin.saml2.settings import OneLogin_Saml2_Settings

        settings_dict = saml_config.build_saml_settings(self.IDP, "https://ai.example.de")
        parsed = OneLogin_Saml2_Settings(settings_dict)
        self.assertEqual(parsed.get_idp_data()["entityId"], self.IDP[saml_config.IDP_ENTITY_ID])
        self.assertTrue(parsed.get_security_data()["wantAssertionsSigned"])

    def test_metadata_is_valid(self):
        """Die Metadaten traegt der Administrator beim Identitaetsanbieter ein —
        sind sie fehlerhaft, scheitert die Einrichtung beim Kunden, nicht bei uns."""
        from onelogin.saml2.settings import OneLogin_Saml2_Settings

        parsed = OneLogin_Saml2_Settings(
            saml_config.build_saml_settings(self.IDP, "https://ai.example.de"))
        metadata = parsed.get_sp_metadata()
        self.assertEqual(parsed.validate_metadata(metadata), [])
        self.assertIn("AssertionConsumerService", metadata)


if __name__ == "__main__":
    unittest.main()
