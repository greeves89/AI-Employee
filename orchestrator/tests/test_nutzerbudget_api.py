"""Monatsbudget je Nutzer im Admin-Bereich (Kosten & Budget).

Den Endpunkt gab es schon, ungeprueft (``body: dict``) und ohne Protokoll; die
Oberflaeche rief ihn nie auf, die Nutzerliste lieferte die Grenze nicht mit.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import roles
from app.models.audit_log import AuditEventType, AuditLog
from app.models.user import User, UserRole

ADMIN = SimpleNamespace(id="admin", role=UserRole.ADMIN)
MITGLIED = SimpleNamespace(id="u2", role=UserRole.MEMBER)


class NutzerBudgetApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(User(id="u1", email="u1@example.invalid", name="U", role=UserRole.MEMBER))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_admin_setzt_und_protokolliert(self):
        async with self.Session() as db:
            r = await roles.set_user_budget("u1", roles.NutzerBudget(budget_usd=25.5), user=ADMIN, db=db)
            self.assertEqual(r["budget_usd"], 25.5)
            eintrag = (await db.execute(select(AuditLog))).scalars().one()
        self.assertEqual(eintrag.event_type, AuditEventType.BUDGET_CHANGED.value)
        self.assertIn("Standard → 25.50 USD", eintrag.command)

    async def test_zurueck_auf_standard_und_ohne_grenze(self):
        async with self.Session() as db:
            await roles.set_user_budget("u1", roles.NutzerBudget(budget_usd=0), user=ADMIN, db=db)
            self.assertEqual((await db.get(User, "u1")).budget_usd, 0)
            await roles.set_user_budget("u1", roles.NutzerBudget(budget_usd=None), user=ADMIN, db=db)
            self.assertIsNone((await db.get(User, "u1")).budget_usd)

    async def test_nur_admins(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await roles.set_user_budget("u1", roles.NutzerBudget(budget_usd=1), user=MITGLIED, db=db)
        self.assertEqual(e.exception.status_code, 403)

    async def test_unbekannter_nutzer(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as e:
                await roles.set_user_budget("weg", roles.NutzerBudget(budget_usd=1), user=ADMIN, db=db)
        self.assertEqual(e.exception.status_code, 404)

    def test_ungueltige_werte(self):
        for wert in (-1, 1_000_001, "viel"):
            with self.subTest(wert), self.assertRaises(ValidationError):
                roles.NutzerBudget(budget_usd=wert)

    def test_standard_in_den_einstellungen_geprueft(self):
        from app.schemas.settings import SettingsUpdate

        self.assertEqual(SettingsUpdate(nutzer_budget_standard_usd=50).nutzer_budget_standard_usd, 50)
        with self.assertRaises(ValidationError):
            SettingsUpdate(nutzer_budget_standard_usd=-5)

    def test_einstellung_ist_speicherbar(self):
        """Sonst scheitert das Speichern still (stille-einstellungen-Falle)."""
        from app.api.settings import _FIELD_MAP
        from app.services.settings_service import ALLOWED_KEYS

        self.assertIn("nutzer_budget_standard_usd", ALLOWED_KEYS)
        self.assertEqual(_FIELD_MAP["nutzer_budget_standard_usd"], "nutzer_budget_standard_usd")

    def test_nutzerliste_liefert_die_grenze(self):
        from app.api.auth import UserResponse

        u = User(id="u1", email="u1@example.invalid", name="U", role=UserRole.MEMBER, is_active=True,
                 approved=True, budget_usd=12.0)
        self.assertEqual(UserResponse.model_validate(u).model_dump()["budget_usd"], 12.0)


if __name__ == "__main__":
    unittest.main()
