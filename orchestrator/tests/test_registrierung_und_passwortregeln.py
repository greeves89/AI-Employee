"""Registrierung geschlossen, SSO-Anlage getrennt, Passwortregeln (#914).

Befund im Markttest: auf dem Produktivsystem stand die Registrierung offen, die
Anmeldeseite zeigte „Register“, das Passwort musste nur 8 Zeichen haben. Und
wer die Registrierung schloss, sperrte damit auch jede neue Microsoft-Anmeldung
aus — beides hing an demselben Schalter.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.config import Settings, settings
from app.core.passwort_regeln import MINDESTLAENGE, passwort_fehler
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.custom_role import CustomRole
from app.models.platform_settings import PlatformSettings
from app.models.user import User, UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


GUT = "Lange-Passphrase-ohne-Sinn"


class Passwortregeln(unittest.TestCase):
    def test_zu_kurz(self):
        self.assertIsNotNone(passwort_fehler("x" * (MINDESTLAENGE - 1)))

    def test_gleich_email(self):
        self.assertIsNotNone(passwort_fehler("anna.beispiel@example.com", "anna.beispiel@example.com"))
        self.assertIsNotNone(passwort_fehler("Anna.Beispiel1", "anna.beispiel1@example.com"))

    def test_haeufige_passwoerter(self):
        for p in ("Passwort2026!!", "password1234", "123456789012", "aaaaaaaaaaaaaa", "Willkommen2025"):
            self.assertIsNotNone(passwort_fehler(p), p)

    def test_gutes_passwort(self):
        self.assertIsNone(passwort_fehler(GUT, "anna@example.com"))


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (CustomRole, User, Agent, AuditLog, PlatformSettings):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self._vorher = {k: getattr(settings, k) for k in
                        ("registration_open", "sso_auto_provisioning", "setup_token", "require_user_approval")}
        settings.setup_token = ""
        settings.require_user_approval = False

    async def asyncTearDown(self):
        for k, v in self._vorher.items():
            setattr(settings, k, v)
        await self.engine.dispose()

    async def _admin_anlegen(self, db):
        db.add(User(id="u1", email="chefin@example.com", name="Chefin", role=UserRole.ADMIN))
        await db.commit()


class NeueAnlage(_Basis):
    def test_vorgaben_fuer_neue_anlagen(self):
        frisch = Settings()
        if "registration_open" not in frisch.model_fields_set:
            self.assertFalse(frisch.registration_open)
        if "sso_auto_provisioning" not in frisch.model_fields_set:
            self.assertTrue(frisch.sso_auto_provisioning)

    async def test_registrieren_ist_geschlossen(self):
        from app.api import auth as auth_api

        settings.registration_open = False
        async with self.Session() as db:
            await self._admin_anlegen(db)
            with self.assertRaises(HTTPException) as ctx:
                await auth_api.register(
                    auth_api.SetupRegisterRequest(name="Neu", email="neu@example.com", password=GUT),
                    SimpleNamespace(set_cookie=lambda *a, **k: None), db,
                )
            self.assertEqual(ctx.exception.status_code, 403)
            status = await auth_api.registration_status(db)
            self.assertFalse(status["registration_open"])

    async def test_sso_legt_trotzdem_an(self):
        from app.services.sso_service import SSOService

        settings.registration_open = False
        settings.sso_auto_provisioning = True
        async with self.Session() as db:
            await self._admin_anlegen(db)
            user = await SSOService(db, None)._find_or_create_user(
                provider_name="microsoft", subject="sub-1", email="neu@example.com",
                name="Neu", email_verified=True,
            )
            self.assertEqual(user.email, "neu@example.com")
            self.assertEqual(user.role, UserRole.UNASSIGNED)
            eintraege = (await db.execute(select(AuditLog).where(AuditLog.event_type == "user_created"))).scalars().all()
            self.assertEqual(len(eintraege), 1)

    async def test_sso_anlage_abschaltbar(self):
        from app.services.sso_service import SSOService

        settings.registration_open = True
        settings.sso_auto_provisioning = False
        async with self.Session() as db:
            await self._admin_anlegen(db)
            with self.assertRaises(ValueError):
                await SSOService(db, None)._find_or_create_user(
                    provider_name="microsoft", subject="sub-2", email="neu2@example.com",
                    name="Neu", email_verified=True,
                )


class Migration(_Basis):
    async def _gespeichert(self, db, key):
        row = await db.get(PlatformSettings, key)
        return row.value if row else None

    async def test_bestandsanlage_behaelt_offene_registrierung(self):
        from app.core.registrierung import registrierung_festschreiben

        settings.registration_open = False  # neue Vorgabe im Speicher
        async with self.Session() as db:
            await self._admin_anlegen(db)
            with patch.object(type(settings), "model_fields_set", new=set(), create=True):
                await registrierung_festschreiben(db)
            self.assertEqual(await self._gespeichert(db, "registration_open"), "true")
            self.assertEqual(await self._gespeichert(db, "sso_auto_provisioning"), "true")
        self.assertTrue(settings.registration_open)

    async def test_bestandsanlage_mit_geschlossener_registrierung_bleibt_ohne_sso_anlage(self):
        """Bisher sperrte registration_open=False auch SSO — das bleibt so."""
        from app.core.registrierung import registrierung_festschreiben

        async with self.Session() as db:
            await self._admin_anlegen(db)
            db.add(PlatformSettings(key="registration_open", value="false"))
            await db.commit()
            settings.registration_open = False
            with patch.object(type(settings), "model_fields_set", new=set(), create=True):
                await registrierung_festschreiben(db)
            self.assertEqual(await self._gespeichert(db, "registration_open"), "false")
            self.assertEqual(await self._gespeichert(db, "sso_auto_provisioning"), "false")

    async def test_neue_anlage_wird_geschlossen_festgeschrieben(self):
        """Sonst hielte der Start nach dem ersten Admin die Anlage für einen Bestand."""
        from app.core.registrierung import registrierung_festschreiben

        settings.registration_open = False
        settings.sso_auto_provisioning = True
        async with self.Session() as db:
            with patch.object(type(settings), "model_fields_set", new=set(), create=True):
                await registrierung_festschreiben(db)
            self.assertEqual(await self._gespeichert(db, "registration_open"), "false")
            # Zweiter Start, inzwischen mit Admin: nichts ändert sich.
            await self._admin_anlegen(db)
            with patch.object(type(settings), "model_fields_set", new=set(), create=True):
                self.assertEqual(await registrierung_festschreiben(db), {})
            self.assertEqual(await self._gespeichert(db, "registration_open"), "false")


class SchwachesPasswortAufAllenWegen(_Basis):
    async def test_registrierung(self):
        from app.api import auth as auth_api

        settings.registration_open = True
        async with self.Session() as db:
            await self._admin_anlegen(db)
            with self.assertRaises(HTTPException) as ctx:
                await auth_api.register(
                    auth_api.SetupRegisterRequest(name="Neu", email="neu@example.com", password="kurz1234"),
                    SimpleNamespace(set_cookie=lambda *a, **k: None), db,
                )
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_anlage_durch_admin(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            await self._admin_anlegen(db)
            admin = await db.get(User, "u1")
            anfrage = SimpleNamespace(json=AsyncMock(return_value={
                "name": "Neu", "email": "neu@example.com", "password": "password1234", "role": "member",
            }))
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=admin)):
                with self.assertRaises(HTTPException) as ctx:
                    await auth_api.create_user(anfrage, db)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIsNone(await db.scalar(select(User).where(User.email == "neu@example.com")))

    async def test_zuruecksetzen_liefert_regelkonformes_passwort(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            await self._admin_anlegen(db)
            db.add(User(id="u2", email="ziel@example.com", name="Ziel", role=UserRole.MEMBER))
            await db.commit()
            admin = await db.get(User, "u1")
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=admin)):
                res = await auth_api.reset_user_password("u2", SimpleNamespace(), db)
            self.assertIsNone(passwort_fehler(res["temp_password"], "ziel@example.com"))
            eintraege = (await db.execute(select(AuditLog).where(AuditLog.event_type == "password_reset"))).scalars().all()
            self.assertEqual([e.user_id for e in eintraege], ["u1"])


class SelbsttestHinweis(unittest.TestCase):
    def test_offene_registrierung_wird_gemeldet(self):
        from app.services.self_test_service import registrierungs_hinweis

        with patch.object(settings, "registration_open", True):
            r = registrierungs_hinweis()
        self.assertEqual(r.status, "warning")
        self.assertIn("Registrierung offen", r.error)

    def test_geschlossen_kein_hinweis(self):
        from app.services.self_test_service import registrierungs_hinweis

        with patch.object(settings, "registration_open", False):
            self.assertEqual(registrierungs_hinweis().status, "passed")


if __name__ == "__main__":
    unittest.main()
