"""Erste Schritte (#1047): jeder Schritt hakt sich am echten Beleg ab — und nur am EIGENEN.

MC/DC je Schritt: ohne Beleg offen; mit eigenem Beleg erledigt; mit fremdem Beleg
(anderer Nutzer) offen. Admins bekommen keine fremden Agenten angerechnet.
Ohne Anmeldung 401; ein fremder Nutzer lässt sich nicht abfragen.
"""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import erste_schritte as api
from app.core import erste_schritte as es
from app.dependencies import get_db, get_redis_service, require_auth
from app.models.agent import Agent
from app.models.ai_account import AIAccount
from app.models.device_token import DeviceToken
from app.models.knowledge import KnowledgeEntry
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.models.schedule import Schedule
from app.models.user import User, UserRole
from app.models.user_ai_credential import UserAiCredential

ADMIN = SimpleNamespace(id="admin", email="admin@example.invalid", role=UserRole.ADMIN,
                        allow_personal_credentials=False)
ICH = SimpleNamespace(id="m1", email="m1@example.invalid", role=UserRole.MEMBER,
                      allow_personal_credentials=True)
ANDERE = "m2"
JETZT = datetime(2026, 10, 27, tzinfo=timezone.utc)
_zaehler = iter(range(10**6))


def _sid():
    return f"s{next(_zaehler)}"

TABELLEN = [User.__table__, Agent.__table__, AIAccount.__table__, KnowledgeEntry.__table__,
            OAuthIntegration.__table__, Schedule.__table__, UserAiCredential.__table__,
            DeviceToken.__table__]


class _Redis:
    def __init__(self):
        self.daten = {}
        self.client = self

    async def exists(self, key):
        return 1 if key in self.daten else 0

    async def set(self, key, wert):
        self.daten[key] = wert


class ErsteSchritte(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=TABELLEN)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            for u in (ADMIN, ICH):
                db.add(User(id=u.id, email=u.email, name=u.id, role=u.role))
            db.add(User(id=ANDERE, email="m2@example.invalid", name="m2", role=UserRole.MEMBER))
            await db.commit()
        self.redis = _Redis()
        # Teamlizenz ohne Schlüssel, keine freigegebenen KI-Konten, keine Gedächtnistabelle
        # (pgvector) und kein Sekretariat — jeder Test schaltet genau seinen Beleg frei.
        for p in (patch("app.core.agent_credentials.team_secret", AsyncMock(return_value=None)),
                  patch("app.api.ai_accounts._allowed_account_ids", AsyncMock(return_value=set())),
                  patch.object(es, "_gedaechtnis", AsyncMock(return_value=False), create=True),
                  patch("app.core.sekretariat.von_nutzer", AsyncMock(return_value=None))):
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _stand(self, user=ICH):
        async with self.sessions() as db:
            s = await es.stand(db, user, self.redis)
        return {x["id"]: x["erledigt"] for x in s["schritte"]}, s

    async def _alles(self, user=ICH):
        """Wie _stand, aber den KI-Zugang immer mit auswerten (Mitglieder sehen ihn sonst nicht)."""
        with patch.object(es, "_ist_admin", return_value=True):
            return await self._stand(user)

    async def _add(self, *objs):
        async with self.sessions() as db:
            db.add_all(objs)
            await db.commit()

    async def test_neu_ist_alles_offen(self):
        erledigt, s = await self._stand()
        self.assertEqual(s["erledigt"], 0)
        self.assertEqual(s["gesamt"], 7, "ohne KI-Zugang sieht auch ein Mitglied diesen Schritt")
        self.assertFalse(any(erledigt.values()))
        self.assertEqual([x["id"] for x in s["schritte"]][-2:], ["iphone_app", "bridge"],
                         "iPhone-App vor der Bridge, die Bridge zuletzt")

    async def test_fremde_belege_zaehlen_nicht(self):
        await self._add(
            Agent(id="fremd", name="Fremd", user_id=ANDERE),
            KnowledgeEntry(title="t", content="c", user_id=ANDERE),
            OAuthIntegration(provider=OAuthProvider.MICROSOFT, user_id=ANDERE,
                             access_token_encrypted="x"),
            Schedule(name="s", agent_id="fremd", enabled=True, prompt="p", interval_seconds=60,
                     id=_sid(), next_run_at=JETZT),
            UserAiCredential(user_id=ANDERE, harness="claude_code", secret_encrypted="x"),
            DeviceToken(user_id=ANDERE, token="fremd", platform="ios"),
        )
        self.redis.daten[es.BRIDGE_MERKER.format(user_id=ANDERE)] = 1
        erledigt, _ = await self._alles()
        self.assertFalse(any(erledigt.values()), erledigt)
        erledigt_admin, _ = await self._stand(ADMIN)
        self.assertFalse(erledigt_admin["agent"], "Admin bekommt fremde Agenten nicht angerechnet")

    async def test_eigene_belege_haken_ab(self):
        await self._add(
            Agent(id="meiner", name="Meiner", user_id=ICH.id),
            KnowledgeEntry(title="t", content="c", user_id=ICH.id),
            Schedule(name="s", agent_id="meiner", enabled=True, prompt="p", interval_seconds=60,
                     id=_sid(), next_run_at=JETZT),
            UserAiCredential(user_id=ICH.id, harness="claude_code", secret_encrypted="x",
                             last_status="ok"),
        )
        self.redis.daten[es.BRIDGE_MERKER.format(user_id=ICH.id)] = 1
        await self._add(OAuthIntegration(provider=OAuthProvider.MICROSOFT, user_id=ICH.id,
                                         access_token_encrypted="x"),
                        DeviceToken(user_id=ICH.id, token="meins", platform="ios"))
        erledigt, s = await self._stand()
        self.assertEqual(erledigt, {k: True for k in erledigt})
        self.assertNotIn("ki_zugang", erledigt, "vorhandener KI-Zugang gehört nicht in die Mitgliederliste")
        self.assertEqual((s["erledigt"], s["gesamt"]), (6, 6))
        _, s_admin = await self._stand(ADMIN)
        self.assertEqual(s_admin["gesamt"], 7, "Admins sehen den KI-Zugang immer")

    async def test_ki_zugang_abgelaufen_oder_gesperrt(self):
        await self._add(UserAiCredential(user_id=ICH.id, harness="claude_code",
                                         secret_encrypted="x", last_status="auth_failed"))
        self.assertFalse((await self._alles())[0]["ki_zugang"], "abgelehnter Zugang zählt nicht")
        gesperrt = SimpleNamespace(**{**vars(ICH), "allow_personal_credentials": False})
        async with self.sessions() as db:
            await db.execute(UserAiCredential.__table__.update().values(last_status="ok"))
            await db.commit()
        with patch("app.core.agent_credentials.personal_credentials_allowed", return_value=False):
            self.assertFalse((await self._alles(gesperrt))[0]["ki_zugang"],
                             "eigener Zugang zählt nicht, wenn eigene Zugänge gesperrt sind")

    async def test_ki_zugang_ueber_team_oder_freigegebenes_konto(self):
        with patch("app.core.agent_credentials.team_secret", AsyncMock(return_value="geheim")):
            self.assertTrue((await self._alles())[0]["ki_zugang"])
        await self._add(AIAccount(id=7, name="Konto", provider_type="anthropic", is_active=True))
        self.assertFalse((await self._alles())[0]["ki_zugang"], "nicht freigegeben")
        with patch("app.api.ai_accounts._allowed_account_ids", AsyncMock(return_value={7})):
            self.assertTrue((await self._alles())[0]["ki_zugang"])

    async def test_geteiltes_microsoft_zaehlt_ki_anbindung_nicht(self):
        await self._add(OAuthIntegration(provider=OAuthProvider.ANTHROPIC, user_id=ICH.id,
                                         access_token_encrypted="x"))
        self.assertFalse((await self._stand())[0]["mail_kalender"], "KI-Zugang ist kein Werkzeug")
        await self._add(OAuthIntegration(provider=OAuthProvider.MICROSOFT, user_id=None,
                                         access_token_encrypted="x"))
        erledigt, _ = await self._stand()
        self.assertTrue(erledigt["mail_kalender"], "von der Verwaltung geteilt")
        self.assertFalse(erledigt["bridge"], "Mail ersetzt die Bridge nicht")

    async def test_bridge_nur_ueber_eigenen_merker(self):
        self.assertFalse((await self._stand())[0]["bridge"])
        self.redis.daten[es.BRIDGE_MERKER.format(user_id=ICH.id)] = 1
        erledigt, _ = await self._stand()
        self.assertTrue(erledigt["bridge"])
        self.assertFalse(erledigt["mail_kalender"], "die Bridge ersetzt Mail und Kalender nicht")

    async def test_ausgeschalteter_zeitplan_zaehlt_nicht(self):
        await self._add(Agent(id="meiner", name="Meiner", user_id=ICH.id),
                        Schedule(name="s", agent_id="meiner", enabled=False, prompt="p",
                                 interval_seconds=60, id=_sid(), next_run_at=JETZT))
        self.assertFalse((await self._stand())[0]["alltag"])

    async def test_mitglied_bekommt_hinweis_admin_nicht(self):
        _, s = await self._stand()
        self.assertEqual(s["schritte"][0]["id"], "ki_zugang", "fehlt der Zugang, sieht ihn auch ein Mitglied")
        self.assertTrue(s["schritte"][0]["hinweis"])
        _, s = await self._stand(ADMIN)
        self.assertIsNone(s["schritte"][0]["hinweis"])


class Schnittstelle(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.app.dependency_overrides[get_db] = lambda: None
        self.app.dependency_overrides[get_redis_service] = lambda: None
        self.client = TestClient(self.app)

    def test_unangemeldet_ist_401(self):
        with patch("app.dependencies._check_users_exist", return_value=True):
            self.assertEqual(self.client.get("/erste-schritte").status_code, 401)

    def test_nur_der_eigene_stand(self):
        self.app.dependency_overrides[require_auth] = lambda: ICH
        with patch.object(es, "stand", AsyncMock(return_value={"schritte": [], "erledigt": 0,
                                                               "gesamt": 5})) as stand:
            r = self.client.get("/erste-schritte?user_id=m2")
        self.assertEqual(r.status_code, 200)
        self.assertIs(stand.await_args.args[1], ICH, "ein fremder Nutzer lässt sich nicht abfragen")


if __name__ == "__main__":
    unittest.main()
