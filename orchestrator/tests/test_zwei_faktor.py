"""Zwei-Faktor-Anmeldung per TOTP für Passwort-Konten (#915).

Befund im Markttest (IT-Sicht): Passwort-Konten hatten keinen zweiten Faktor.
Geprüft wird VERHALTEN: was die Anmeldung zurückgibt, ob ein Zwischen-Token
irgendwo als Zugang durchgeht, ob ein Code zweimal gilt, ob die Sperre greift,
ob Wiederherstellungscodes einmalig sind und wer wessen Zwei-Faktor ändern darf.

MC/DC-Matrix der Anmeldeentscheidung (``naechster_schritt``):

  SSO | Passwort ok | MFA aktiv | erzwungen | Ergebnis
  ----+-------------+-----------+-----------+-----------
   0  |      1      |     0     |     0     | ZUGANG       (Basis)
   0  |      0      |     0     |     0     | ABGELEHNT    (Passwort kippt)
   0  |      1      |     1     |     0     | CODE         (MFA kippt)
   0  |      1      |     0     |     1     | EINRICHTEN   (Pflicht kippt)
   1  |      1      |     1     |     1     | ZUGANG       (SSO kippt gegen CODE)
   0  |      0      |     1     |     1     | ABGELEHNT    (falsches Passwort verrät nichts)
   0  |      1      |     1     |     1     | CODE         (aktiv schlägt Pflicht)
"""

import itertools
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.config import settings
from app.core import zwei_faktor as zf
from app.core.auth import create_access_token, decode_token, hash_password
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.custom_role import CustomRole
from app.models.platform_settings import PlatformSettings
from app.models.user import User, UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


PASSWORT = "Lange-Passphrase-ohne-Sinn"


class FakeRedis:
    """So streng wie nötig: SET NX, INCR, TTL, DELETE — mehr braucht die Prüfung nicht."""

    def __init__(self):
        self.daten: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.daten:
            return None
        self.daten[key] = str(value)
        return True

    async def get(self, key):
        return self.daten.get(key)

    async def incr(self, key):
        self.daten[key] = str(int(self.daten.get(key, "0")) + 1)
        return int(self.daten[key])

    async def ttl(self, key):
        return 900 if key in self.daten else -2

    async def delete(self, *keys):
        for k in keys:
            self.daten.pop(k, None)


class _Antwort:
    def __init__(self):
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    def set_cookie(self, name, value, **kw):
        self.cookies[name] = value


class Entscheidung(unittest.TestCase):
    """Die reine Anmeldeentscheidung — MC/DC plus die vollständige Tabelle."""

    def _s(self, sso, pw, mfa, pflicht):
        return zf.naechster_schritt(sso_anmeldung=sso, passwort_ok=pw, mfa_aktiv=mfa, erzwungen=pflicht)

    def test_mcdc(self):
        S = zf.Schritt
        self.assertEqual(self._s(False, True, False, False), S.ZUGANG)
        self.assertEqual(self._s(False, False, False, False), S.ABGELEHNT)
        self.assertEqual(self._s(False, True, True, False), S.CODE)
        self.assertEqual(self._s(False, True, False, True), S.EINRICHTEN)
        self.assertEqual(self._s(True, True, True, True), S.ZUGANG)
        self.assertEqual(self._s(False, False, True, True), S.ABGELEHNT)
        self.assertEqual(self._s(False, True, True, True), S.CODE)

    def test_vollstaendige_tabelle(self):
        S = zf.Schritt
        for sso, pw, mfa, pflicht in itertools.product((False, True), repeat=4):
            ergebnis = self._s(sso, pw, mfa, pflicht)
            if sso:
                erwartet = S.ZUGANG
            elif not pw:
                erwartet = S.ABGELEHNT
            elif mfa:
                erwartet = S.CODE
            elif pflicht:
                erwartet = S.EINRICHTEN
            else:
                erwartet = S.ZUGANG
            self.assertEqual(ergebnis, erwartet, (sso, pw, mfa, pflicht))


class Totp(unittest.TestCase):
    def test_rfc6238_testvektor(self):
        # RFC 6238, Anhang B, SHA-1, Schlüssel "12345678901234567890" — 8 Stellen
        # dort; die letzten 6 Stellen sind der 6-stellige Code desselben Zeitschritts.
        import base64

        geheimnis = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(zf.code_generieren(geheimnis, 59), "287082")
        self.assertEqual(zf.code_generieren(geheimnis, 1111111109), "081804")

    def test_toleranz_ein_zeitfenster(self):
        g = zf.neues_geheimnis()
        jetzt = 1_800_000_000
        self.assertIsNotNone(zf.passender_zeitschritt(g, zf.code_generieren(g, jetzt - 30), jetzt))
        self.assertIsNotNone(zf.passender_zeitschritt(g, zf.code_generieren(g, jetzt + 30), jetzt))
        self.assertIsNone(zf.passender_zeitschritt(g, zf.code_generieren(g, jetzt - 90), jetzt))
        self.assertIsNone(zf.passender_zeitschritt(g, "abcdef", jetzt))

    def test_otpauth_uri(self):
        uri = zf.otpauth_uri("JBSWY3DPEHPK3PXP", "anna@example.com")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=JBSWY3DPEHPK3PXP", uri)
        self.assertIn("period=30", uri)
        self.assertIn("digits=6", uri)


class PendingToken(unittest.TestCase):
    def test_kein_zugangstoken(self):
        nutzer = SimpleNamespace(id="u1", token_version=0)
        token = zf.pending_token_erstellen(nutzer, zf.ZWECK_CODE)
        # Der normale Dekoder lehnt es ab — jede Stelle, die decode_token nutzt,
        # ist damit sicher, auch die ohne eigene Typprüfung.
        with self.assertRaises(Exception):
            decode_token(token)
        daten = zf.pending_token_pruefen(token)
        self.assertEqual(daten["type"], zf.TYP_PENDING)
        self.assertEqual(daten["zweck"], zf.ZWECK_CODE)

    def test_zugangstoken_ist_kein_pending_token(self):
        with self.assertRaises(Exception):
            zf.pending_token_pruefen(create_access_token("u1", "admin", 0))


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (CustomRole, User, Agent, AuditLog, PlatformSettings):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self._vorher = {k: getattr(settings, k) for k in
                        ("require_mfa_for_password_accounts", "sso_only_login")}
        settings.require_mfa_for_password_accounts = False
        settings.sso_only_login = False
        # Echte Fernet-Verschlüsselung mit einem Testschlüssel — nur im Speicher.
        from cryptography.fernet import Fernet
        from app.core import encryption

        self._schluessel_vorher = encryption._cached_key
        encryption._cached_key = Fernet.generate_key().decode()
        self.redis = FakeRedis()
        self.request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(redis=SimpleNamespace(client=self.redis))),
            cookies={}, headers={},
        )
        async with self.Session() as db:
            db.add(User(id="u1", email="chefin@example.com", name="Chefin", role=UserRole.ADMIN,
                        password_hash=hash_password(PASSWORT)))
            db.add(User(id="u2", email="anna@example.com", name="Anna", role=UserRole.MEMBER,
                        password_hash=hash_password(PASSWORT)))
            db.add(User(id="u3", email="bert@example.com", name="Bert", role=UserRole.MEMBER,
                        password_hash=hash_password(PASSWORT)))
            await db.commit()

    async def asyncTearDown(self):
        from app.api import auth as auth_api

        from app.core import encryption

        encryption._cached_key = self._schluessel_vorher
        for k, v in self._vorher.items():
            setattr(settings, k, v)
        for mail in ("chefin@example.com", "anna@example.com", "bert@example.com"):
            auth_api._clear_login_attempts(mail)
        await self.engine.dispose()

    # --- Helfer -----------------------------------------------------------

    async def _login(self, db, email="anna@example.com", passwort=PASSWORT):
        from app.api import auth as auth_api

        antwort = _Antwort()
        daten = await auth_api.login(auth_api.LoginRequest(email=email, password=passwort),
                                     self.request, antwort, db)
        return daten, antwort

    async def _mfa_aktivieren(self, db, uid="u2") -> str:
        """Zwei-Faktor für ``uid`` direkt einrichten, Geheimnis zurückgeben."""
        user = await db.get(User, uid)
        geheimnis = zf.neues_geheimnis()
        user.totp_secret_encrypted = zf.geheimnis_verschluesseln(geheimnis)
        from datetime import datetime, timezone
        user.mfa_enabled_at = datetime.now(timezone.utc)
        codes, ablage = zf.wiederherstellungscodes_erzeugen()
        user.mfa_recovery_codes = ablage
        self.codes = codes
        await db.commit()
        return geheimnis

    async def _verify(self, db, token, code):
        from app.api import zwei_faktor as zf_api

        antwort = _Antwort()
        daten = await zf_api.verify(zf_api.MfaVerifyRequest(mfa_token=token, code=code),
                                    self.request, antwort, db)
        return daten, antwort

    async def _eintraege(self, db, typ):
        return (await db.execute(select(AuditLog).where(AuditLog.event_type == typ))).scalars().all()


class Anmeldung(_Basis):
    async def test_ohne_mfa_wie_bisher(self):
        async with self.Session() as db:
            daten, antwort = await self._login(db)
            self.assertIn("access_token", daten)
            self.assertIn("access_token", antwort.cookies)
            self.assertNotIn("mfa_required", daten)

    async def test_mfa_aktiv_gibt_nur_pending_token(self):
        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            daten, antwort = await self._login(db)
            self.assertTrue(daten["mfa_required"])
            self.assertFalse(daten["mfa_setup_required"])
            self.assertNotIn("access_token", daten)
            self.assertNotIn("user", daten)
            self.assertEqual(antwort.cookies, {})
            # Ohne Code keine erfolgreiche Anmeldung im Protokoll.
            self.assertEqual(await self._eintraege(db, "login_succeeded"), [])

    async def test_falsches_passwort_verraet_mfa_nicht(self):
        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            with self.assertRaises(HTTPException) as ctx:
                await self._login(db, passwort="Falsch-Falsch-Falsch")
            self.assertEqual(ctx.exception.status_code, 401)

    async def test_erzwungen_ohne_einrichtung_fuehrt_in_die_einrichtung(self):
        settings.require_mfa_for_password_accounts = True
        async with self.Session() as db:
            daten, antwort = await self._login(db)
            self.assertTrue(daten["mfa_required"])
            self.assertTrue(daten["mfa_setup_required"])
            self.assertEqual(antwort.cookies, {})

    async def test_erzwungen_gilt_auch_fuer_verknuepftes_sso_konto_mit_passwort(self):
        """Wer mit Passwort kommt, braucht den zweiten Faktor — auch wenn das Konto
        zusätzlich mit SSO verknüpft ist. Sonst wäre das Verknüpfen ein Umweg."""
        settings.require_mfa_for_password_accounts = True
        async with self.Session() as db:
            user = await db.get(User, "u2")
            user.sso_provider, user.sso_subject = "microsoft", "sub-anna"
            await db.commit()
            daten, _ = await self._login(db)
            self.assertTrue(daten["mfa_setup_required"])

    async def test_sso_anmeldung_ist_ausgenommen(self):
        from app.api import auth as auth_api

        settings.require_mfa_for_password_accounts = True
        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            user = await db.get(User, "u2")
            antwort = await auth_api.finish_sso_login(user, None, "microsoft", "https://example.invalid", db=db)
            gesetzt = antwort.headers.getlist("set-cookie")
            self.assertTrue(any(c.startswith("access_token=") for c in gesetzt))

    async def test_code_meldet_an(self):
        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            ergebnis, antwort = await self._verify(db, daten["mfa_token"],
                                                   zf.code_generieren(geheimnis, time.time()))
            self.assertEqual(ergebnis["user"]["id"], "u2")
            self.assertIn("access_token", antwort.cookies)
            self.assertEqual(len(await self._eintraege(db, "login_succeeded")), 1)

    async def test_falscher_code_wird_protokolliert(self):
        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, daten["mfa_token"], "000000")
            self.assertEqual(ctx.exception.status_code, 401)
            [eintrag] = await self._eintraege(db, "login_failed")
            self.assertEqual(eintrag.user_id, "u2")
            self.assertNotIn("000000", repr(eintrag.meta) + repr(eintrag.command))


class PendingTokenIstKeinZugang(_Basis):
    async def test_nicht_als_cookie_und_nicht_als_bearer(self):
        from app.dependencies import get_current_user, get_current_user_ws

        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            token = daten["mfa_token"]
            for anfrage in (
                SimpleNamespace(cookies={"access_token": token}, headers={}),
                SimpleNamespace(cookies={}, headers={"Authorization": f"Bearer {token}"}),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await get_current_user(anfrage, db)
                self.assertEqual(ctx.exception.status_code, 401)
            self.assertIsNone(await get_current_user_ws(token, db))

    async def test_nicht_als_refresh(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            anfrage = SimpleNamespace(cookies={"refresh_token": daten["mfa_token"]}, headers={})
            with self.assertRaises(HTTPException) as ctx:
                await auth_api.refresh_token(anfrage, _Antwort(), db)
            self.assertEqual(ctx.exception.status_code, 401)

    async def test_code_token_oeffnet_keine_einrichtung(self):
        """Ein Token für die Code-Eingabe darf kein neues Geheimnis setzen."""
        from app.api import zwei_faktor as zf_api

        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            with self.assertRaises(HTTPException) as ctx:
                await zf_api.pending_einrichtung_starten(
                    zf_api.MfaTokenRequest(mfa_token=daten["mfa_token"]), _Antwort(), db)
            self.assertEqual(ctx.exception.status_code, 401)

    async def test_zuruecksetzen_entwertet_pending_token(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            admin = await db.get(User, "u1")
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=admin)):
                await auth_api.reset_user_mfa("u2", self.request, db)
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, daten["mfa_token"], zf.code_generieren(geheimnis, time.time()))
            self.assertEqual(ctx.exception.status_code, 401)


class ReplayUndSperre(_Basis):
    async def test_code_gilt_nur_einmal(self):
        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
            code = zf.code_generieren(geheimnis, time.time())
            daten, _ = await self._login(db)
            await self._verify(db, daten["mfa_token"], code)
            daten2, _ = await self._login(db)
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, daten2["mfa_token"], code)
            self.assertEqual(ctx.exception.status_code, 401)

    async def test_sperre_nach_fuenf_fehlversuchen(self):
        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            for _ in range(zf.MAX_FEHLVERSUCHE):
                with self.assertRaises(HTTPException):
                    await self._verify(db, daten["mfa_token"], "000000")
            # Auch der richtige Code hilft jetzt nicht mehr.
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, daten["mfa_token"], zf.code_generieren(geheimnis, time.time()))
            self.assertEqual(ctx.exception.status_code, 429)

    async def test_ohne_redis_kein_durchwinken(self):
        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            self.request.app.state.redis = SimpleNamespace(client=None)
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, daten["mfa_token"], zf.code_generieren(geheimnis, time.time()))
            self.assertEqual(ctx.exception.status_code, 503)


class Wiederherstellungscodes(_Basis):
    async def test_einmalig(self):
        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            code = self.codes[0]
            daten, _ = await self._login(db)
            ergebnis, _ = await self._verify(db, daten["mfa_token"], code)
            self.assertEqual(ergebnis["user"]["id"], "u2")
            daten2, _ = await self._login(db)
            with self.assertRaises(HTTPException):
                await self._verify(db, daten2["mfa_token"], code)
            user = await db.get(User, "u2")
            self.assertEqual(zf.verbleibende_codes(user), zf.ANZAHL_WIEDERHERSTELLUNGSCODES - 1)

    async def test_nur_als_hash_gespeichert(self):
        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            user = await db.get(User, "u2")
            for code in self.codes:
                self.assertNotIn(code, user.mfa_recovery_codes)
                self.assertNotIn(code.replace("-", ""), user.mfa_recovery_codes)
            self.assertEqual(len(json.loads(user.mfa_recovery_codes)), zf.ANZAHL_WIEDERHERSTELLUNGSCODES)


class EinrichtenUndAbschalten(_Basis):
    async def _als(self, uid):
        async with self.Session() as db:
            return await db.get(User, uid)

    async def test_einrichten_in_den_eigenen_einstellungen(self):
        from app.api import zwei_faktor as zf_api

        anna = await self._als("u2")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=anna)):
            async with self.Session() as db:
                antwort = _Antwort()
                start = await zf_api.einrichten_starten(zf_api.MfaPasswortRequest(password=PASSWORT),
                                                        self.request, antwort, db)
                self.assertEqual(antwort.headers.get("Cache-Control"), "no-store")
                self.assertIn("<svg", start["qr_svg"])
                self.assertTrue(start["otpauth_uri"].startswith("otpauth://totp/"))
                user = await db.get(User, "u2")
                # Geheimnis liegt verschlüsselt, Zwei-Faktor ist noch NICHT aktiv.
                self.assertNotIn(start["secret"], user.totp_secret_encrypted)
                self.assertIsNone(user.mfa_enabled_at)
                ergebnis = await zf_api.einrichten_bestaetigen(
                    zf_api.MfaCodeRequest(code=zf.code_generieren(start["secret"], time.time())),
                    self.request, _Antwort(), db)
                self.assertEqual(len(ergebnis["recovery_codes"]), zf.ANZAHL_WIEDERHERSTELLUNGSCODES)
                await db.refresh(user)
                self.assertIsNotNone(user.mfa_enabled_at)
                self.assertEqual(len(await self._eintraege(db, "mfa_enabled")), 1)

    async def test_einrichten_nur_mit_passwort(self):
        """Ein gestohlenes Sitzungstoken allein darf keine fremde App einrichten."""
        from app.api import zwei_faktor as zf_api

        anna = await self._als("u2")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=anna)):
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as ctx:
                    await zf_api.einrichten_starten(zf_api.MfaPasswortRequest(password="Falsch-Falsch-Falsch"),
                                                    self.request, _Antwort(), db)
                self.assertEqual(ctx.exception.status_code, 403)
                self.assertIsNone((await db.get(User, "u2")).totp_secret_encrypted)

    async def test_abschalten_ohne_code_abgelehnt(self):
        from app.api import zwei_faktor as zf_api

        async with self.Session() as db:
            await self._mfa_aktivieren(db)
        anna = await self._als("u2")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=anna)):
            async with self.Session() as db:
                for code in ("", "000000"):
                    with self.assertRaises(HTTPException):
                        await zf_api.abschalten(zf_api.MfaAbschaltenRequest(password=PASSWORT, code=code),
                                                self.request, db)
                user = await db.get(User, "u2")
                self.assertIsNotNone(user.mfa_enabled_at)

    async def test_abschalten_ohne_passwort_abgelehnt(self):
        from app.api import zwei_faktor as zf_api

        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
        anna = await self._als("u2")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=anna)):
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as ctx:
                    await zf_api.abschalten(zf_api.MfaAbschaltenRequest(
                        password="Falsch-Falsch-Falsch", code=zf.code_generieren(geheimnis, time.time())),
                        self.request, db)
                # 403, nicht 401: auf 401 erneuert die Oberfläche und wiederholt die Anfrage.
                self.assertEqual(ctx.exception.status_code, 403)
                user = await db.get(User, "u2")
                self.assertIsNotNone(user.mfa_enabled_at)

    async def test_abschalten_mit_passwort_und_code(self):
        from app.api import zwei_faktor as zf_api

        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
        anna = await self._als("u2")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=anna)):
            async with self.Session() as db:
                await zf_api.abschalten(zf_api.MfaAbschaltenRequest(
                    password=PASSWORT, code=zf.code_generieren(geheimnis, time.time())), self.request, db)
                user = await db.get(User, "u2")
                self.assertIsNone(user.mfa_enabled_at)
                self.assertIsNone(user.totp_secret_encrypted)
                self.assertEqual(len(await self._eintraege(db, "mfa_disabled")), 1)

    async def test_erzwungene_einrichtung_meldet_danach_an(self):
        from app.api import zwei_faktor as zf_api

        settings.require_mfa_for_password_accounts = True
        async with self.Session() as db:
            daten, _ = await self._login(db)
            token = daten["mfa_token"]
            start = await zf_api.pending_einrichtung_starten(zf_api.MfaTokenRequest(mfa_token=token), _Antwort(), db)
            antwort = _Antwort()
            ergebnis = await zf_api.pending_einrichtung_bestaetigen(
                zf_api.MfaVerifyRequest(mfa_token=token, code=zf.code_generieren(start["secret"], time.time())),
                self.request, antwort, db)
            self.assertEqual(ergebnis["user"]["id"], "u2")
            self.assertEqual(len(ergebnis["recovery_codes"]), zf.ANZAHL_WIEDERHERSTELLUNGSCODES)
            self.assertIn("access_token", antwort.cookies)

    async def test_einrichtungs_token_meldet_ohne_code_nicht_an(self):
        from app.api import zwei_faktor as zf_api

        settings.require_mfa_for_password_accounts = True
        async with self.Session() as db:
            daten, _ = await self._login(db)
            token = daten["mfa_token"]
            await zf_api.pending_einrichtung_starten(zf_api.MfaTokenRequest(mfa_token=token), _Antwort(), db)
            with self.assertRaises(HTTPException):
                await zf_api.pending_einrichtung_bestaetigen(
                    zf_api.MfaVerifyRequest(mfa_token=token, code="000000"), self.request, _Antwort(), db)
            # Und als Code-Token taugt es auch nicht.
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, token, "000000")
            self.assertEqual(ctx.exception.status_code, 401)


class Verwaltung(_Basis):
    async def test_admin_setzt_zurueck_und_beendet_sitzungen(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            vorher = (await db.get(User, "u2")).token_version
            admin = await db.get(User, "u1")
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=admin)):
                await auth_api.reset_user_mfa("u2", self.request, db)
            user = await db.get(User, "u2")
            self.assertIsNone(user.mfa_enabled_at)
            self.assertIsNone(user.totp_secret_encrypted)
            self.assertIsNone(user.mfa_recovery_codes)
            self.assertEqual(user.token_version, vorher + 1)
            [eintrag] = await self._eintraege(db, "mfa_reset")
            self.assertEqual(eintrag.user_id, "u1")
            self.assertEqual(eintrag.meta.get("target_user_id"), "u2")

    async def test_admin_setzt_den_eigenen_nicht_hier_zurueck(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            await self._mfa_aktivieren(db, "u1")
            admin = await db.get(User, "u1")
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=admin)):
                with self.assertRaises(HTTPException) as ctx:
                    await auth_api.reset_user_mfa("u1", self.request, db)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIsNotNone((await db.get(User, "u1")).mfa_enabled_at)

    async def test_nur_admin_darf_zuruecksetzen(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            await self._mfa_aktivieren(db)
            bert = await db.get(User, "u3")
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=bert)):
                with self.assertRaises(HTTPException) as ctx:
                    await auth_api.reset_user_mfa("u2", self.request, db)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertIsNotNone((await db.get(User, "u2")).mfa_enabled_at)

    async def test_nutzer_aendert_nur_den_eigenen_zweiten_faktor(self):
        """Die Selbstbedienung kennt keine fremde Nutzer-ID: Bert schaltet Annas
        Zwei-Faktor nicht ab, auch nicht mit Annas Code."""
        from app.api import zwei_faktor as zf_api

        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db, "u2")
            bert = await db.get(User, "u3")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=bert)):
            async with self.Session() as db:
                with self.assertRaises(HTTPException):
                    await zf_api.abschalten(zf_api.MfaAbschaltenRequest(
                        password=PASSWORT, code=zf.code_generieren(geheimnis, time.time())), self.request, db)
                self.assertIsNotNone((await db.get(User, "u2")).mfa_enabled_at)
                status = await zf_api.status(self.request, db)
                self.assertFalse(status["enabled"])

    async def test_schalter_ist_speicherbar(self):
        """Ohne Eintrag in ALLOWED_KEYS scheiterte das Speichern still."""
        from app.services.settings_service import ALLOWED_KEYS

        self.assertIn("require_mfa_for_password_accounts", ALLOWED_KEYS)
        from app.api.settings import _FIELD_MAP

        self.assertIn("require_mfa_for_password_accounts", _FIELD_MAP)


if __name__ == "__main__":
    unittest.main()
