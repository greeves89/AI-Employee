"""Die Brain-Liste zeigt nur, was der Nutzer nutzen darf.

29.09.2026: Jeder angemeldete Nutzer sah alle Second Brains samt Beschreibung —
anhaengen durfte er nur freigegebene. Beide Fragen beantwortet jetzt dieselbe
Funktion (``freigegebene_mounts``).
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import brains as api
from app.models.second_brain import SecondBrain
from app.models.user import UserRole
from app.models.user_mount_access import UserMountAccess

ADMIN = SimpleNamespace(id="admin", role=UserRole.ADMIN)
MIT_ROLLE = SimpleNamespace(id="m1", role=UserRole.MEMBER)
MIT_EIGENER = SimpleNamespace(id="m2", role=UserRole.MEMBER)
OHNE = SimpleNamespace(id="m3", role=UserRole.MEMBER)


class BrainListeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(SecondBrain.metadata.create_all,
                                tables=[SecondBrain.__table__, UserMountAccess.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        for slug in ("it", "hr"):
            self.db.add(SecondBrain(label=f"brain-{slug}", name=slug.upper(), slug=slug,
                                    host_path=f"/x/{slug}", container_path=f"/y/{slug}", default_mode="ro"))
        self.db.add(UserMountAccess(user_id="m2", mount_label="brain-hr", mode="ro"))
        await self.db.commit()

        async def perms(user, db):
            return {"mount_labels": ["brain-it"] if user.id == "m1" else None}

        self.p = patch("app.core.permissions.get_effective_permissions", perms)
        self.p.start()

    async def asyncTearDown(self):
        self.p.stop()
        await self.db.close()
        await self.engine.dispose()

    async def namen(self, user):
        return [b.name if hasattr(b, "name") else b["name"] for b in await api.list_brains(user=user, db=self.db)]

    async def test_admin_sieht_alle(self):
        self.assertEqual(await self.namen(ADMIN), ["HR", "IT"])

    async def test_freigabe_ueber_rolle(self):
        self.assertEqual(await self.namen(MIT_ROLLE), ["IT"])

    async def test_persoenliche_freigabe(self):
        self.assertEqual(await self.namen(MIT_EIGENER), ["HR"])

    async def test_ohne_freigabe_keine(self):
        self.assertEqual(await self.namen(OHNE), [])


if __name__ == "__main__":
    unittest.main()
