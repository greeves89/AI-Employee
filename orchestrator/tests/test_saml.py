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

from app.core import saml_config
from app.models.custom_role import CustomRole

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


# Die Gruppen-zu-Rolle-Zuordnung selbst (frueher hier als RoleMappingTests) ist nach
# app/core/sso_group_roles.py gewandert — gemeinsam mit dem Microsoft-OIDC-Login,
# siehe tests/test_sso_group_roles.py. Diese Datei prueft nur noch die SAML-eigene
# Extraktion (``extract_groups``) und den Zuordnungs-Riegel drumherum.


class ApplyGroupRoleTests(unittest.IsolatedAsyncioTestCase):
    """Die Rollenaenderung selbst — hier kann man echten Schaden anrichten.

    Die Zuordnung liegt seit der Vereinheitlichung mit Microsoft-OIDC nicht mehr in
    einem uebergebenen dict, sondern in ``sso_group_role_mappings`` — deshalb echtes
    SQLite statt eines Mock-``db``: ein Mock haette genau die Query weggetestet, die
    hier den Unterschied macht (siehe test_sso_group_roles.py fuer die Aufloesung
    selbst, hier geht es um die Rollenaenderung + den Admin-Schutz drumherum).
    """

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.base import Base
        from app.models.sso_group_mapping import SsoGroupRoleMapping
        from app.models.sso_observed_group import SsoObservedGroup
        from app.models.user import User

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c, tables=[User.__table__, SsoGroupRoleMapping.__table__, SsoObservedGroup.__table__,
                               CustomRole.__table__]
                )
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    def _service(self):
        from app.services.sso_service import SSOService

        svc = SSOService.__new__(SSOService)
        svc.db = self.db
        return svc

    async def _user(self, role, *, other_admins=0):
        """Der Nutzer, dessen Rolle sich aendern soll, plus optional weitere
        Administratoren — so entscheidet der echte COUNT(*), nicht ein Mock-Wert."""
        from app.models.user import User, UserRole

        for i in range(other_admins):
            self.db.add(User(id=f"other-{i}", email=f"other{i}@b.de", name="x",
                              role=UserRole.ADMIN, approved=True))
        user = User(id="u1", email="a@b.de", name="a", role=role, approved=True)
        self.db.add(user)
        await self.db.commit()
        return user

    async def _mapping(self, group_name, role):
        from app.models.sso_group_mapping import SsoGroupRoleMapping
        self.db.add(SsoGroupRoleMapping(
            provider="saml", group_name=group_name, target_kind="role", target_value=role,
        ))
        await self.db.commit()

    async def test_promotes_to_admin(self):
        from app.models.user import UserRole
        await self._mapping("IT", "admin")
        user = await self._user(UserRole.MEMBER)
        changed = await self._service().apply_group_role(user, "saml", ["IT"])
        self.assertTrue(changed)
        self.assertEqual(user.role, UserRole.ADMIN)

    async def test_no_match_leaves_the_role_alone(self):
        from app.models.user import UserRole
        await self._mapping("IT", "admin")
        user = await self._user(UserRole.MANAGER)
        self.assertFalse(await self._service().apply_group_role(user, "saml", ["Fremde"]))
        self.assertEqual(user.role, UserRole.MANAGER)

    async def test_last_admin_is_not_demoted(self):
        """Sonst sperrt eine Gruppenzuordnung die Plattform aus."""
        from app.models.user import UserRole
        await self._mapping("Alle", "member")
        user = await self._user(UserRole.ADMIN, other_admins=0)
        changed = await self._service().apply_group_role(user, "saml", ["Alle"])
        self.assertFalse(changed)
        self.assertEqual(user.role, UserRole.ADMIN)

    async def test_demotion_works_when_other_admins_remain(self):
        from app.models.user import UserRole
        await self._mapping("Alle", "member")
        user = await self._user(UserRole.ADMIN, other_admins=2)
        changed = await self._service().apply_group_role(user, "saml", ["Alle"])
        self.assertTrue(changed)
        self.assertEqual(user.role, UserRole.MEMBER)

    async def _custom_role(self, role_id=7):
        self.db.add(CustomRole(id=role_id, name="Vertrieb-Rolle", permissions={}))
        await self.db.commit()

    async def test_custom_role_target_sets_member_floor_plus_custom_role_id(self):
        from app.models.sso_group_mapping import SsoGroupRoleMapping
        from app.models.user import UserRole

        await self._custom_role()
        self.db.add(SsoGroupRoleMapping(
            provider="saml", group_name="Vertrieb", target_kind="custom_role", target_value="7",
        ))
        await self.db.commit()
        user = await self._user(UserRole.MEMBER)
        changed = await self._service().apply_group_role(user, "saml", ["Vertrieb"])
        self.assertTrue(changed)
        self.assertEqual(user.role, UserRole.MEMBER)
        self.assertEqual(user.custom_role_id, 7)

    async def test_custom_role_target_also_respects_last_admin_guard(self):
        from app.models.sso_group_mapping import SsoGroupRoleMapping
        from app.models.user import UserRole

        await self._custom_role()
        self.db.add(SsoGroupRoleMapping(
            provider="saml", group_name="Vertrieb", target_kind="custom_role", target_value="7",
        ))
        await self.db.commit()
        user = await self._user(UserRole.ADMIN, other_admins=0)
        changed = await self._service().apply_group_role(user, "saml", ["Vertrieb"])
        self.assertFalse(changed)
        self.assertEqual(user.role, UserRole.ADMIN)
        self.assertIsNone(user.custom_role_id)

    async def test_dangling_custom_role_reference_is_skipped_not_applied(self):
        """Zeigt die Zuordnung auf eine geloeschte CustomRole (Admin hat die Rolle
        entfernt, aber die Zuordnung stehen lassen), darf das NICHT still
        durchrutschen — siehe Security-Review 2026-08-13."""
        from app.models.sso_group_mapping import SsoGroupRoleMapping
        from app.models.user import UserRole

        self.db.add(SsoGroupRoleMapping(
            provider="saml", group_name="Vertrieb", target_kind="custom_role", target_value="999",
        ))
        await self.db.commit()
        user = await self._user(UserRole.MEMBER)
        changed = await self._service().apply_group_role(user, "saml", ["Vertrieb"])
        self.assertFalse(changed)
        self.assertIsNone(user.custom_role_id)

    async def test_deactivated_admins_do_not_count_as_protection(self):
        """Ein abgeschaltetes Admin-Konto kann sich nicht mehr anmelden und faengt
        niemanden auf — es mitzuzaehlen waere ein Lockout, der wie ein Schutz
        aussieht. Siehe Security-Review 2026-08-13."""
        from app.models.user import User, UserRole

        await self._mapping("Alle", "member")
        self.db.add(User(id="inactive-admin", email="ia@b.de", name="x",
                          role=UserRole.ADMIN, approved=True, is_active=False))
        user = await self._user(UserRole.ADMIN, other_admins=0)
        changed = await self._service().apply_group_role(user, "saml", ["Alle"])
        self.assertFalse(changed, "der inaktive Admin darf nicht als zweiter Admin zaehlen")
        self.assertEqual(user.role, UserRole.ADMIN)


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


class RedirectTargetTests(unittest.TestCase):
    """Das Rueckkehrziel darf NIE aus der Anwendung herausfuehren.

    Bei SAML kommt es als ``RelayState`` mit der Antwort des Identitaetsanbieters —
    also aus einer Quelle, die der Anmeldende beeinflussen kann (der Wert ist nicht
    Teil der signierten Assertion). Ein ungeprueftes Ziel waere eine offene
    Weiterleitung: Angreifer schickt jemanden ueber die echte Anmeldung und leitet
    ihn danach auf eine nachgebaute Seite.

    Geprueft wird ``finish_sso_login`` selbst — die EINE Stelle, an der jedes
    Anmeldeverfahren sein Ziel bestimmt. Die Pruefung dort zu haben (statt an jeder
    Aufrufstelle) ist Absicht: eine neue Anmeldeart kann sie so nicht vergessen.
    """

    FRONTEND = "https://ai.example.de"

    def _redirect_for(self, return_to):
        import asyncio
        from types import SimpleNamespace
        from app.api.auth import finish_sso_login
        from app.models.user import UserRole

        user = SimpleNamespace(id="u1", email="a@b.de", role=UserRole.MEMBER, approved=True, token_version=0)
        resp = asyncio.run(finish_sso_login(user, return_to, "saml", self.FRONTEND))
        return resp.headers["location"]

    def test_hostile_targets_fall_back_to_the_dashboard(self):
        hostile = [
            "https://evil.example/phish",      # absolute Adresse
            "//evil.example/phish",            # protokollrelativ
            "/\\evil.example",                 # Backslash-Variante
            "\\\\evil.example",
            "javascript:alert(1)",
            "http://evil.example",
            "evil.example/pfad",               # ohne fuehrenden Schraegstrich
        ]
        for value in hostile:
            with self.subTest(relay=value):
                location = self._redirect_for(value)
                self.assertEqual(location, f"{self.FRONTEND}/dashboard")
                self.assertNotIn("evil.example", location)

    def test_empty_and_missing_are_safe(self):
        for value in ("", None, "   "):
            with self.subTest(relay=value):
                self.assertEqual(self._redirect_for(value), f"{self.FRONTEND}/dashboard")

    def test_an_overlong_target_is_rejected(self):
        self.assertEqual(self._redirect_for("/" + "a" * 2500), f"{self.FRONTEND}/dashboard")

    def test_a_genuine_internal_target_still_works(self):
        """Die Pruefung darf den eigentlichen Zweck nicht kaputt machen."""
        self.assertEqual(self._redirect_for("/agents/abc123"),
                         f"{self.FRONTEND}/agents/abc123")

    def test_the_check_lives_in_the_shared_tail(self):
        """Nicht an den Aufrufstellen: sonst sieht es aus, als laege die
        Zustaendigkeit dort, und jemand entfernt sie hier."""
        src = (ORCH / "app/api/auth.py").read_text()
        tail = src.split("def finish_sso_login")[1].split("\n# --- SAML")[0]
        self.assertIn("safe_internal_path(return_to)", tail)


class SettingsPathTests(unittest.TestCase):
    """Vier Stellen, sonst meldet die Oberflaeche „Gespeichert." und nichts passiert."""

    FIELDS = ("saml_idp_entity_id", "saml_idp_sso_url", "saml_idp_x509_cert",
              "saml_group_attribute")

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

    def test_group_role_mapping_points_to_the_dedicated_admin_panel(self):
        """Die freie JSON-Textbox ist weg (siehe SsoGroupsPanelTests) — die
        SAML-Einrichtungsseite muss dorthin verweisen, statt eine tote Referenz
        auf eine Eingabe zu hinterlassen, die es nicht mehr gibt."""
        src = (REPO / "frontend/src/components/settings/saml-config.tsx").read_text()
        self.assertNotIn("saml_group_role_map", src)
        self.assertIn("SSO-Gruppen", src)


class SsoGroupsPanelTests(unittest.TestCase):
    """Die JSON-Textbox ist durch eine strukturierte Verwaltung ersetzt — beobachtete
    Gruppen zum Anklicken, Ziel waehlbar zwischen fester Rolle und CustomRole."""

    SRC = (REPO / "frontend/src/components/admin/sso-groups-panel.tsx").read_text()

    def test_offers_both_target_kinds(self):
        self.assertIn('"role"', self.SRC)
        self.assertIn('"custom_role"', self.SRC)

    def test_shows_observed_groups_to_click_instead_of_type(self):
        self.assertIn("unmappedObserved", self.SRC)

    def test_wired_into_the_admin_console(self):
        src = (REPO / "frontend/src/app/admin/page.tsx").read_text()
        self.assertIn("SsoGroupsPanel", src)


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
