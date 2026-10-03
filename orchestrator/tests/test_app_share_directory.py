"""Personenliste der Freigabe-Dialoge (`GET /apps/directory`).

Nicht-Admins bekommen nur id + Anzeigename — keine E-Mail-Adressen der Kolleginnen
und Kollegen. Admins sehen weiterhin die E-Mail. Ohne Anmeldung gibt es 401.

MC/DC ueber (angemeldet, Admin): nein -> 401; ja/nein -> ohne E-Mail; ja/ja -> mit E-Mail.
Der Aufrufer selbst steht nie in der Liste.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import apps_overview as api
from app.dependencies import get_db, require_auth
from app.models.user import User, UserRole

ADMIN = SimpleNamespace(id="admin", email="admin@example.invalid", role=UserRole.ADMIN)
MITGLIED = SimpleNamespace(id="m1", email="m1@example.invalid", role=UserRole.MEMBER)
ANDERES = SimpleNamespace(id="m2", email="m2@example.invalid", role=UserRole.MEMBER)


class AppShareDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=[User.__table__])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            for u, name in ((ADMIN, "Anna"), (MITGLIED, "Berta"), (ANDERES, "Carla")):
                db.add(User(id=u.id, email=u.email, name=name, role=u.role))
            await db.commit()
        self.app = FastAPI()
        self.app.include_router(api.router)

        async def _db():
            async with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = _db
        self.client = TestClient(self.app)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _als(self, user):
        self.app.dependency_overrides[require_auth] = lambda: user

    def test_nicht_admin_sieht_keine_email(self):
        self._als(MITGLIED)
        r = self.client.get("/apps/directory")
        self.assertEqual(r.status_code, 200)
        users = r.json()["users"]
        self.assertEqual({u["id"] for u in users}, {"admin", "m2"})
        for u in users:
            self.assertEqual(set(u), {"id", "name"})
        self.assertNotIn("@", r.text)

    def test_admin_sieht_die_email(self):
        self._als(ADMIN)
        users = self.client.get("/apps/directory").json()["users"]
        self.assertEqual({u["id"]: u["email"] for u in users},
                         {"m1": "m1@example.invalid", "m2": "m2@example.invalid"})

    def test_unangemeldet_ist_401(self):
        with patch("app.dependencies._check_users_exist", return_value=True):
            r = self.client.get("/apps/directory")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
