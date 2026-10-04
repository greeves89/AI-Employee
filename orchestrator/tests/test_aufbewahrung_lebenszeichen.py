"""Aufbewahrungsfristen und abschaltbares Lebenszeichen (#892, Punkte 5 und 6).

Befund aus dem Markttest: Prüfprotokoll und Chatverläufe wuchsen unbegrenzt,
eine Frist ließ sich nirgends einstellen. Und das tägliche Lebenszeichen an den
Lizenzserver war nur per SQL abschaltbar.

Geprüft wird hier VERHALTEN gegen eine echte (SQLite-)Datenbank:

* Frist leer/0 löscht nichts; Frist N löscht nur, was älter ist.
* Ein Lauf mit Löschungen hinterlässt GENAU EINEN Protokolleintrag, und der
  überlebt auch den nächsten Lauf.
* Die Einstellungen lassen sich über den echten ``SettingsService`` speichern
  (fehlt ein Schlüssel in ``ALLOWED_KEYS``, scheitert das sonst still) — und nur
  ein Administrator darf sie ändern.
* Lebenszeichen-Schalter aus → es wird nichts gesendet.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import aufbewahrung
from app.db.session import get_db
from app.models.agent import Agent
from app.models.audit_log import AuditEventType, AuditLog
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.platform_settings import PlatformSettings
from app.models.user import UserRole
from app.services import license_heartbeat_service as lebenszeichen
from app.services.settings_service import SettingsService

JETZT = datetime(2026, 10, 4, 3, 0, tzinfo=timezone.utc)
TABELLEN = [AuditLog.__table__, ChatMessage.__table__, ChatSession.__table__,
            PlatformSettings.__table__, Agent.__table__]


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AuditLog.metadata.create_all, tables=TABELLEN)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _setze(self, **werte):
        async with self.Session() as db:
            svc = SettingsService(db)
            for k, v in werte.items():
                await svc.set(k, v)
            await db.commit()

    async def _protokoll(self, alter_tage: int, art: str = "login_succeeded"):
        async with self.Session() as db:
            db.add(AuditLog(agent_id="auth", event_type=art, outcome="success",
                            created_at=JETZT - timedelta(days=alter_tage)))
            await db.commit()

    async def _nachricht(self, alter_tage: int, sitzung: str = "s1", nr: int = 0):
        async with self.Session() as db:
            db.add(ChatMessage(agent_id="a1", session_id=sitzung, message_id=f"m{alter_tage}-{nr}",
                               role="user", content="Hallo",
                               timestamp=JETZT - timedelta(days=alter_tage)))
            await db.commit()

    async def _anzahl(self, modell, *bedingungen) -> int:
        async with self.Session() as db:
            return (await db.execute(select(func.count()).select_from(modell).where(*bedingungen))).scalar()

    async def _lauf(self, now=JETZT):
        return await aufbewahrung.tick(session_factory=self.Session, now=now)


class FristTests(_MitDatenbank):
    async def test_ohne_frist_wird_nichts_geloescht(self):
        await self._protokoll(4000)
        await self._nachricht(4000)
        await self._lauf()
        self.assertEqual(await self._anzahl(AuditLog), 1)
        self.assertEqual(await self._anzahl(ChatMessage), 1)

    async def test_frist_null_heisst_unbegrenzt(self):
        await self._setze(retention_audit_days="0", retention_chat_days="")
        await self._protokoll(4000)
        await self._nachricht(4000)
        await self._lauf()
        self.assertEqual(await self._anzahl(AuditLog), 1)
        self.assertEqual(await self._anzahl(ChatMessage), 1)

    async def test_frist_loescht_nur_aelteres(self):
        await self._setze(retention_audit_days="90", retention_chat_days="30")
        for tage in (10, 89, 91, 400):
            await self._protokoll(tage)
        for tage in (1, 29, 31, 200):
            await self._nachricht(tage)
        await self._lauf()

        bleibt = await self._anzahl(AuditLog, AuditLog.event_type == "login_succeeded")
        self.assertEqual(bleibt, 2, "nur die zwei Einträge älter als 90 Tage dürfen weg sein")
        self.assertEqual(await self._anzahl(ChatMessage), 2)
        self.assertEqual(
            await self._anzahl(ChatMessage, ChatMessage.timestamp < JETZT - timedelta(days=30)), 0)

    async def test_nur_chatfrist_laesst_das_protokoll_unberuehrt(self):
        await self._setze(retention_chat_days="30")
        await self._protokoll(4000)
        await self._nachricht(60)
        await self._lauf()
        self.assertEqual(await self._anzahl(AuditLog, AuditLog.event_type == "login_succeeded"), 1)
        self.assertEqual(await self._anzahl(ChatMessage), 0)

    async def test_viele_zeilen_werden_in_stapeln_vollstaendig_geloescht(self):
        await self._setze(retention_chat_days="30")
        async with self.Session() as db:
            for i in range(25):
                db.add(ChatMessage(agent_id="a1", session_id="s1", message_id=f"x{i}", role="user",
                                   content="", timestamp=JETZT - timedelta(days=60)))
            await db.commit()
        with patch.object(aufbewahrung, "STAPEL", 7):
            await self._lauf()
        self.assertEqual(await self._anzahl(ChatMessage), 0)

    async def test_leere_alte_gespraeche_verschwinden_aktive_bleiben(self):
        await self._setze(retention_chat_days="30")
        async with self.Session() as db:
            alt = JETZT - timedelta(days=60)
            db.add(ChatSession(agent_id="a1", session_id="alt", title="Alt",
                               created_at=alt, updated_at=alt))
            db.add(ChatSession(agent_id="a1", session_id="aktiv", title="Aktiv",
                               created_at=alt, updated_at=alt))
            await db.commit()
        await self._nachricht(60, sitzung="alt")
        await self._nachricht(2, sitzung="aktiv")
        await self._lauf()
        async with self.Session() as db:
            uebrig = (await db.execute(select(ChatSession.session_id))).scalars().all()
        self.assertEqual(uebrig, ["aktiv"])

    async def test_frist_unter_der_untergrenze_wird_angehoben(self):
        """Wer an der Oberfläche vorbei 1 Tag einträgt, leert das Protokoll trotzdem nicht."""
        await self._setze(retention_audit_days="1")
        await self._protokoll(5)
        await self._protokoll(aufbewahrung.MINDEST_TAGE_AUDIT + 5)
        await self._lauf()
        self.assertEqual(await self._anzahl(AuditLog, AuditLog.event_type == "login_succeeded"), 1)


class LaufprotokollTests(_MitDatenbank):
    async def _laufeintraege(self):
        async with self.Session() as db:
            return (await db.execute(select(AuditLog).where(
                AuditLog.event_type == AuditEventType.RETENTION_PURGED.value))).scalars().all()

    async def test_ein_lauf_schreibt_genau_einen_eintrag_mit_anzahl_und_frist(self):
        await self._setze(retention_audit_days="90", retention_chat_days="30")
        await self._protokoll(100)
        await self._protokoll(200)
        await self._nachricht(40)
        await self._lauf()
        eintraege = await self._laufeintraege()
        self.assertEqual(len(eintraege), 1)
        meta = eintraege[0].meta
        self.assertEqual(meta["audit_geloescht"], 2)
        self.assertEqual(meta["audit_frist_tage"], 90)
        self.assertEqual(meta["chat_nachrichten_geloescht"], 1)
        self.assertEqual(meta["chat_frist_tage"], 30)

    async def test_ohne_loeschungen_kein_eintrag(self):
        await self._setze(retention_audit_days="90")
        await self._protokoll(10)
        await self._lauf()
        self.assertEqual(await self._laufeintraege(), [])

    async def test_der_eintrag_des_laufs_ueberlebt_den_naechsten_lauf(self):
        await self._setze(retention_audit_days=str(aufbewahrung.MINDEST_TAGE_AUDIT))
        await self._protokoll(400)
        await self._lauf()
        self.assertEqual(len(await self._laufeintraege()), 1)
        # Nächster Tag, wieder etwas Altes da: der alte Laufeintrag bleibt stehen.
        await self._protokoll(400)
        await self._lauf(now=JETZT + timedelta(days=1))
        self.assertEqual(len(await self._laufeintraege()), 2)

    async def test_hoechstens_ein_lauf_am_tag(self):
        await self._setze(retention_audit_days="90")
        await self._protokoll(100)
        await self._lauf()
        await self._protokoll(100)
        await self._lauf(now=JETZT + timedelta(hours=2))
        self.assertEqual(await self._anzahl(AuditLog, AuditLog.event_type == "login_succeeded"), 1,
                         "zweiter Lauf am selben Tag darf nicht laufen")
        await self._lauf(now=JETZT + timedelta(hours=24))
        self.assertEqual(await self._anzahl(AuditLog, AuditLog.event_type == "login_succeeded"), 0)


class _Api(_MitDatenbank):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.api import settings as settings_api

        app = FastAPI()
        app.include_router(settings_api.router)

        async def _db():
            async with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = _db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def asyncTearDown(self):
        await self.client.aclose()
        await super().asyncTearDown()

    async def _als(self, rolle, methode, **kw):
        nutzer = SimpleNamespace(id="u1", role=rolle)
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=nutzer)):
            return await self.client.request(methode, "/settings/datenschutz", **kw)


class EinstellungTests(_Api):
    async def test_admin_speichert_und_liest_alle_drei_werte(self):
        antwort = await self._als(UserRole.ADMIN, "PUT", json={
            "audit_aufbewahrung_tage": 365, "chat_aufbewahrung_tage": 90, "lebenszeichen_aktiv": False})
        self.assertEqual(antwort.status_code, 200, antwort.text)
        stand = (await self._als(UserRole.ADMIN, "GET")).json()
        self.assertEqual(stand["audit_aufbewahrung_tage"], 365)
        self.assertEqual(stand["chat_aufbewahrung_tage"], 90)
        self.assertFalse(stand["lebenszeichen_aktiv"])

    async def test_voreinstellung_unbegrenzt_und_lebenszeichen_an(self):
        stand = (await self._als(UserRole.ADMIN, "GET")).json()
        self.assertIsNone(stand["audit_aufbewahrung_tage"])
        self.assertIsNone(stand["chat_aufbewahrung_tage"])
        self.assertTrue(stand["lebenszeichen_aktiv"])

    async def test_leer_setzt_zurueck_auf_unbegrenzt(self):
        await self._als(UserRole.ADMIN, "PUT", json={"audit_aufbewahrung_tage": 365})
        await self._als(UserRole.ADMIN, "PUT", json={"audit_aufbewahrung_tage": None})
        stand = (await self._als(UserRole.ADMIN, "GET")).json()
        self.assertIsNone(stand["audit_aufbewahrung_tage"])

    async def test_nicht_mitgeschickte_werte_bleiben_stehen(self):
        await self._als(UserRole.ADMIN, "PUT", json={"chat_aufbewahrung_tage": 90})
        await self._als(UserRole.ADMIN, "PUT", json={"lebenszeichen_aktiv": False})
        stand = (await self._als(UserRole.ADMIN, "GET")).json()
        self.assertEqual(stand["chat_aufbewahrung_tage"], 90)

    async def test_frist_unter_der_untergrenze_wird_abgewiesen(self):
        antwort = await self._als(UserRole.ADMIN, "PUT", json={"audit_aufbewahrung_tage": 5})
        self.assertEqual(antwort.status_code, 422)
        stand = (await self._als(UserRole.ADMIN, "GET")).json()
        self.assertIsNone(stand["audit_aufbewahrung_tage"])

    async def test_nur_admin_darf_aendern_und_lesen(self):
        for rolle in (UserRole.MEMBER, UserRole.MANAGER, UserRole.VIEWER):
            with self.subTest(rolle=rolle):
                antwort = await self._als(rolle, "PUT", json={"audit_aufbewahrung_tage": 365,
                                                              "lebenszeichen_aktiv": False})
                self.assertEqual(antwort.status_code, 403)
                self.assertEqual((await self._als(rolle, "GET")).status_code, 403)
        stand = (await self._als(UserRole.ADMIN, "GET")).json()
        self.assertIsNone(stand["audit_aufbewahrung_tage"])
        self.assertTrue(stand["lebenszeichen_aktiv"])

    async def test_aenderung_steht_im_pruefprotokoll(self):
        await self._als(UserRole.ADMIN, "PUT", json={"audit_aufbewahrung_tage": 365})
        self.assertEqual(await self._anzahl(
            AuditLog, AuditLog.event_type == AuditEventType.SETTINGS_CHANGED.value), 1)


class LebenszeichenSchalterTests(_Api):
    async def _ping(self) -> list:
        gesendet: list = []

        class _Client:
            async def __aenter__(self_inner): return self_inner
            async def __aexit__(self_inner, *a): return False
            async def post(self_inner, url, json=None, headers=None):
                gesendet.append(json)
                return SimpleNamespace(status_code=500, json=lambda: {})

        dienst = lebenszeichen.LicenseHeartbeatService(self.Session)
        with patch.object(lebenszeichen.httpx, "AsyncClient", lambda **k: _Client()):
            await dienst._ping()
        return gesendet

    async def test_standard_an_es_wird_gesendet(self):
        self.assertEqual(len(await self._ping()), 1)

    async def test_ueber_den_schalter_aus_nichts_wird_gesendet(self):
        antwort = await self._als(UserRole.ADMIN, "PUT", json={"lebenszeichen_aktiv": False})
        self.assertEqual(antwort.status_code, 200, antwort.text)
        self.assertEqual(await self._ping(), [])

    async def test_wieder_an_es_wird_wieder_gesendet(self):
        await self._als(UserRole.ADMIN, "PUT", json={"lebenszeichen_aktiv": False})
        await self._als(UserRole.ADMIN, "PUT", json={"lebenszeichen_aktiv": True})
        self.assertEqual(len(await self._ping()), 1)


if __name__ == "__main__":
    unittest.main()
