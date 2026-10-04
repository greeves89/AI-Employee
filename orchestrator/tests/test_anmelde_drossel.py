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

    IP-Grenze (gemeinsam für /auth/login und /auth/mfa/verify)
    bis zur Grenze Fehlversuche                      normal (401)
    danach, auch mit richtigem Passwort              429
    andere IP                                        unberührt
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select

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

