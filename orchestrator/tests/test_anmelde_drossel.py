"""Fehlanmeldungen fluten das Prüfprotokoll nicht, Durchprobieren wird je IP gebremst.

Sicherheitsprüfung v1.362.0, F8 (#908): Jede Fehlanmeldung schrieb eine Zeile ins
Prüfprotokoll — auch für erfundene E-Mail-Adressen, ungedrosselt. Wer die
Anmeldung mit wechselnden Adressen beschießt, füllte so das Protokoll (und
verdrängte darin die echten Ereignisse). Außerdem gab es nur eine Grenze je
E-Mail-Adresse, keine je Absender.

    Fall                                             Prüfprotokoll
    falsches Passwort, bekanntes Konto               je Versuch ein Eintrag (wie bisher)
    unbekannte Adresse, erster der Stunde            ein Eintrag
    unbekannte Adresse, weitere in der Stunde        gezählt, nur ab und zu ein Zwischenstand
    erster Fehlversuch der neuen Stunde              Zusammenfassung der vorigen Stunde
    ohne Redis                                       dasselbe, Zähler im Prozess

    Schreibvariante eines bestehenden Kontos         einzeln, dem Konto zugeordnet
    Eintrag konnte nicht geschrieben werden          der nächste Fall meldet erneut
    erfolgreiche Anmeldung nach vielen Fehlern       sichtbar wie immer
    SSO-Fehler nach Bestätigung durch den Anbieter   einzeln
    SSO-Fehler davor (z. B. ungültiger state)        gesammelt

    IP-Grenze (gemeinsam für /auth/login und /auth/mfa/verify) — Nachprüfung:
    bis zur Grenze Fehlversuche                      normal (401)
    danach, auch mit richtigem Passwort              429 — nur für DIESE Adresse
    andere IP, auch für dasselbe Konto               unberührt
    keine / nur Proxy-Adresse ermittelbar            keine IP-Sperre
    Fenster vorbei                                   wieder frei (gleitend, kurz)
    hinter vertrautem Proxy mit X-Forwarded-For      je echter Client-Adresse
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.core import anmelde_drossel as drossel
from app.core import zwei_faktor as zf
from app.models.audit_log import AuditLog
from tests.test_zwei_faktor import PASSWORT, _Basis


class _DrosselBasis(_Basis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        drossel._zuruecksetzen()
        self.request.client = SimpleNamespace(host="203.0.113.5")

    async def asyncTearDown(self):
        drossel._zuruecksetzen()
        await super().asyncTearDown()

    def _von(self, ip):
        self.request.client = SimpleNamespace(host=ip)

    async def _fehlversuch(self, db, email, passwort="Falsch-Falsch-Falsch"):
        with self.assertRaises(HTTPException) as ctx:
            await self._login(db, email=email, passwort=passwort)
        return ctx.exception.status_code

    async def _fehllogins(self, db):
        return (await db.execute(
            select(AuditLog).where(AuditLog.event_type == "login_failed").order_by(AuditLog.id)
        )).scalars().all()


class UnbekannteAdressen(_DrosselBasis):
    async def test_viele_erfundene_adressen_kaum_eintraege(self):
        async with self.Session() as db:
            for i in range(50):
                self._von(f"198.51.100.{i}")  # wechselnde Absender: IP-Grenze greift hier nicht
                self.assertEqual(await self._fehlversuch(db, f"gibt-es-nicht-{i}@example.com"), 401)
            eintraege = await self._fehllogins(db)
            self.assertGreaterEqual(len(eintraege), 1)
            self.assertLessEqual(len(eintraege), 3, [e.command for e in eintraege])
            # Der Zwischenstand nennt die Zahl, nicht jede einzelne Adresse.
            self.assertGreaterEqual(max(e.meta.get("anzahl", 1) for e in eintraege), 10)

    async def test_bekanntes_konto_weiter_einzeln(self):
        async with self.Session() as db:
            for _ in range(3):
                self.assertEqual(await self._fehlversuch(db, "anna@example.com"), 401)
            eintraege = await self._fehllogins(db)
            self.assertEqual(len(eintraege), 3)
            self.assertTrue(all(e.user_id == "u2" for e in eintraege))

    async def test_neue_stunde_fasst_die_vorige_zusammen(self):
        jetzt = 1_800_000_000.0
        async with self.Session() as db:
            with patch("app.core.anmelde_drossel.time.time", return_value=jetzt):
                for i in range(4):
                    self._von(f"198.51.100.{i}")
                    await self._fehlversuch(db, f"x{i}@example.com")
            with patch("app.core.anmelde_drossel.time.time", return_value=jetzt + 3600):
                self._von("198.51.100.200")
                await self._fehlversuch(db, "y@example.com")
            texte = [e.command for e in await self._fehllogins(db)]
            zusammenfassung = [e for e in await self._fehllogins(db)
                               if (e.meta or {}).get("zusammenfassung")]
            self.assertEqual(len(zusammenfassung), 1, texte)
            self.assertEqual(zusammenfassung[0].meta["anzahl"], 4)

    async def test_ohne_redis_ebenso_gesammelt(self):
        self.request.app.state.redis = SimpleNamespace(client=None)
        async with self.Session() as db:
            for i in range(30):
                self._von(f"198.51.100.{i}")
                await self._fehlversuch(db, f"z{i}@example.com")
            self.assertLessEqual(len(await self._fehllogins(db)), 3)


class IpGrenze(_DrosselBasis):
    async def test_ab_der_grenze_429_auch_mit_richtigem_passwort(self):
        async with self.Session() as db:
            for i in range(drossel.IP_MAX_FEHLVERSUCHE):
                self.assertEqual(await self._fehlversuch(db, f"raten-{i}@example.com"), 401)
            self.assertEqual(await self._fehlversuch(db, "bert@example.com", passwort=PASSWORT), 429)
            # Ein anderer Absender ist nicht betroffen.
            self._von("192.0.2.77")
            daten, _ = await self._login(db, email="bert@example.com")
            self.assertIn("access_token", daten)

    async def test_erfolgreiche_anmeldungen_zaehlen_nicht(self):
        """Viele Leute hinter einer Firmen-IP sollen sich weiter anmelden können."""
        async with self.Session() as db:
            for _ in range(drossel.IP_MAX_FEHLVERSUCHE + 5):
                daten, _ = await self._login(db, email="bert@example.com")
                self.assertIn("access_token", daten)

    async def test_code_schritt_teilt_die_grenze(self):
        async with self.Session() as db:
            geheimnis = await self._mfa_aktivieren(db)
            daten, _ = await self._login(db)
            # Codes für wechselnde Konten raten — hier: 000000 bis zur Grenze, mit
            # immer neuem Zwischen-Token, damit die Sperre je Konto nicht vorher greift.
            for _ in range(drossel.IP_MAX_FEHLVERSUCHE):
                self.redis.daten.pop("mfa:fehlversuche:u2", None)
                with self.assertRaises(HTTPException) as ctx:
                    await self._verify(db, daten["mfa_token"], "000000")
                self.assertEqual(ctx.exception.status_code, 401)
            with self.assertRaises(HTTPException) as ctx:
                await self._verify(db, daten["mfa_token"], zf.code_generieren(geheimnis, time.time()))
            self.assertEqual(ctx.exception.status_code, 429)



class SchreibvariantenBestehenderKonten(_DrosselBasis):
    async def test_grossschreibung_wird_dem_konto_zugeordnet(self):
        async with self.Session() as db:
            for _ in range(3):
                await self._fehlversuch(db, "ANNA@example.com")
            eintraege = await self._fehllogins(db)
            self.assertEqual(len(eintraege), 3)
            self.assertTrue(all(e.user_id == "u2" for e in eintraege))

    async def test_leerzeichen_und_unicode_varianten(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            for variante in (" Anna@Example.com ", "\uff41nna@example.com"):
                await auth_api._fehlanmeldung_protokollieren(db, self.request, email=variante, user=None)
            eintraege = await self._fehllogins(db)
            self.assertEqual([e.user_id for e in eintraege], ["u2", "u2"])


class KeineStilleLuecke(_DrosselBasis):
    async def test_misslungener_eintrag_wird_nachgeholt(self):
        from app.api import auth as auth_api

        echt = auth_api.protokolliere
        aufrufe = {"n": 0}

        async def erst_kaputt(*args, **kwargs):
            aufrufe["n"] += 1
            if aufrufe["n"] == 1:
                return None  # Eintrag konnte nicht vorgemerkt werden
            return await echt(*args, **kwargs)

        async with self.Session() as db:
            with patch("app.api.auth.protokolliere", erst_kaputt):
                for i in range(2):
                    self._von(f"198.51.100.{i}")
                    await self._fehlversuch(db, f"weg-{i}@example.com")
            self.assertEqual(len(await self._fehllogins(db)), 1)

    async def test_anmeldung_nach_vielen_fehlern_sichtbar(self):
        async with self.Session() as db:
            for i in range(30):
                self._von(f"198.51.100.{i}")
                await self._fehlversuch(db, f"raten-{i}@example.com")
            self._von("192.0.2.10")
            await self._login(db)
            erfolg = (await db.execute(select(AuditLog).where(
                AuditLog.event_type == "login_succeeded"))).scalars().all()
            self.assertEqual([e.user_id for e in erfolg], ["u2"])


class SsoFehler(_DrosselBasis):
    async def test_nach_bestaetigung_einzeln_davor_gesammelt(self):
        from app.api import auth as auth_api

        async with self.Session() as db:
            for _ in range(3):
                await auth_api._sso_fehler_protokollieren(
                    db, self.request, "microsoft", ValueError("Account is deactivated"))
            for _ in range(3):
                await auth_api._sso_fehler_protokollieren(
                    db, self.request, "microsoft", ValueError("Invalid or expired SSO state"))
            gruende = [e.meta.get("grund") for e in await self._fehllogins(db)]
            self.assertEqual(gruende.count("Account is deactivated"), 3)
            self.assertEqual(gruende.count("Invalid or expired SSO state"), 1)


class IpSperreNurFuerDieseAdresse(_DrosselBasis):
    async def test_konto_bleibt_von_anderer_adresse_erreichbar(self):
        async with self.Session() as db:
            for _ in range(6):
                await self._fehlversuch(db, "bert@example.com")
            # Von dieser Adresse ist bert jetzt gebremst …
            self.assertEqual(await self._fehlversuch(db, "bert@example.com", passwort=PASSWORT), 429)
            # … von einer anderen nicht.
            self._von("192.0.2.77")
            daten, _ = await self._login(db, email="bert@example.com")
            self.assertIn("access_token", daten)

    async def test_ohne_ermittelbare_adresse_keine_ip_sperre(self):
        async with self.Session() as db:
            for fall in (None, SimpleNamespace(host="172.18.0.5")):
                drossel._zuruecksetzen()
                self.request.client = fall
                self.request.headers = {"cf-ray": "abc"} if fall else {}
                for i in range(drossel.IP_MAX_FEHLVERSUCHE + 5):
                    self.assertEqual(await self._fehlversuch(db, f"p{i}-{id(fall)}@example.com"), 401)

    async def test_sperre_gleitet_ab(self):
        t0 = 1_800_000_000.0
        async with self.Session() as db:
            with patch("app.core.anmelde_drossel.time.time", return_value=t0):
                for i in range(drossel.IP_MAX_FEHLVERSUCHE):
                    await self._fehlversuch(db, f"g{i}@example.com")
                self.assertEqual(await self._fehlversuch(db, "g-x@example.com"), 429)
            with patch("app.core.anmelde_drossel.time.time",
                       return_value=t0 + (drossel.IP_FENSTER_MINUTEN + 1) * 60):
                self.assertEqual(await self._fehlversuch(db, "g-y@example.com"), 401)

    async def test_hinter_vertrautem_proxy_je_echter_adresse(self):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        from app.core.client_ip import eindeutige_client_ip

        gesehen = []

        async def innen(scope, receive, send):
            gesehen.append(eindeutige_client_ip(Request(scope)))

        async def lauf(app, xff):
            scope = {"type": "http", "method": "POST", "path": "/api/v1/auth/login",
                     "headers": [(b"x-forwarded-for", xff.encode())], "client": ("172.18.0.2", 5000),
                     "scheme": "http", "query_string": b"", "server": ("orchestrator", 8000)}
            await app(scope, None, None)

        vertraut = ProxyHeadersMiddleware(innen, trusted_hosts="*")
        await lauf(vertraut, "203.0.113.7")
        await lauf(vertraut, "198.51.100.8")
        self.assertEqual(gesehen, ["203.0.113.7", "198.51.100.8"])
        # Nicht vertrauter Absender: X-Forwarded-For zählt nicht, und die interne
        # Adresse mit Proxy-Spur ist keine eindeutige — also keine IP-Sperre.
        gesehen.clear()
        await lauf(ProxyHeadersMiddleware(innen, trusted_hosts="127.0.0.1"), "203.0.113.7")
        self.assertEqual(gesehen, [None])
