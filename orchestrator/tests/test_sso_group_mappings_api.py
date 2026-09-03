"""Admin-CRUD fuer IdP-Gruppen-Zuordnungen: Validierung + der eigentliche Zweck der
Beobachtungs-Liste (Gruppen zum Anklicken statt Abtippen anbieten).
"""

import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.sso_group_mappings import (
    MappingCreate,
    MappingUpdate,
    create_mapping,
    delete_mapping,
    list_mappings,
    list_observed_groups,
    update_mapping,
)
from app.models.base import Base
from app.models.custom_role import CustomRole
from app.models.sso_group_mapping import SsoGroupRoleMapping
from app.models.sso_observed_group import SsoObservedGroup

ADMIN = SimpleNamespace(id="admin-1", role=None)


class SsoGroupMappingsApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c, tables=[
                        SsoGroupRoleMapping.__table__, SsoObservedGroup.__table__,
                        CustomRole.__table__,
                    ]
                )
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_create_role_mapping(self):
        row = await create_mapping(
            body=MappingCreate(provider="microsoft", group_name="Vertrieb",
                                target_kind="role", target_value="member"),
            user=ADMIN, db=self.db,
        )
        self.assertEqual(row["group_name"], "Vertrieb")
        self.assertEqual(row["target_kind"], "role")

    async def test_invalid_provider_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await create_mapping(
                body=MappingCreate(provider="okta", group_name="X",
                                    target_kind="role", target_value="member"),
                user=ADMIN, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_invalid_role_name_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await create_mapping(
                body=MappingCreate(provider="microsoft", group_name="X",
                                    target_kind="role", target_value="gottkaiser"),
                user=ADMIN, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_custom_role_target_must_exist(self):
        with self.assertRaises(HTTPException) as ctx:
            await create_mapping(
                body=MappingCreate(provider="microsoft", group_name="X",
                                    target_kind="custom_role", target_value="999"),
                user=ADMIN, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_custom_role_target_accepted_when_it_exists(self):
        self.db.add(CustomRole(id=7, name="Vertrieb-Rolle", permissions={}))
        await self.db.commit()
        row = await create_mapping(
            body=MappingCreate(provider="microsoft", group_name="Vertrieb",
                                target_kind="custom_role", target_value="7"),
            user=ADMIN, db=self.db,
        )
        self.assertEqual(row["custom_role_name"], "Vertrieb-Rolle")

    async def test_duplicate_group_per_provider_is_rejected(self):
        body = MappingCreate(provider="microsoft", group_name="Vertrieb",
                              target_kind="role", target_value="member")
        await create_mapping(body=body, user=ADMIN, db=self.db)
        with self.assertRaises(HTTPException) as ctx:
            await create_mapping(body=body, user=ADMIN, db=self.db)
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_same_group_name_allowed_on_a_different_provider(self):
        for provider in ("microsoft", "saml"):
            await create_mapping(
                body=MappingCreate(provider=provider, group_name="Vertrieb",
                                    target_kind="role", target_value="member"),
                user=ADMIN, db=self.db,
            )
        result = await list_mappings(provider=None, user=ADMIN, db=self.db)
        self.assertEqual(len(result["mappings"]), 2)

    async def test_update_changes_target(self):
        created = await create_mapping(
            body=MappingCreate(provider="microsoft", group_name="Vertrieb",
                                target_kind="role", target_value="member"),
            user=ADMIN, db=self.db,
        )
        updated = await update_mapping(
            mapping_id=created["id"],
            body=MappingUpdate(target_value="manager"),
            user=ADMIN, db=self.db,
        )
        self.assertEqual(updated["target_value"], "manager")

    async def test_delete_removes_it(self):
        created = await create_mapping(
            body=MappingCreate(provider="microsoft", group_name="Vertrieb",
                                target_kind="role", target_value="member"),
            user=ADMIN, db=self.db,
        )
        await delete_mapping(mapping_id=created["id"], user=ADMIN, db=self.db)
        result = await list_mappings(provider=None, user=ADMIN, db=self.db)
        self.assertEqual(result["mappings"], [])

    async def test_observed_groups_flag_which_are_already_mapped(self):
        self.db.add_all([
            SsoObservedGroup(provider="microsoft", group_name="Vertrieb"),
            SsoObservedGroup(provider="microsoft", group_name="Marketing"),
        ])
        await self.db.commit()
        await create_mapping(
            body=MappingCreate(provider="microsoft", group_name="Vertrieb",
                                target_kind="role", target_value="member"),
            user=ADMIN, db=self.db,
        )
        result = await list_observed_groups(provider="microsoft", user=ADMIN, db=self.db)
        by_name = {g["group_name"]: g["mapped"] for g in result["groups"]}
        self.assertTrue(by_name["Vertrieb"])
        self.assertFalse(by_name["Marketing"])

    async def test_observed_groups_scoped_to_provider(self):
        self.db.add_all([
            SsoObservedGroup(provider="microsoft", group_name="Vertrieb"),
            SsoObservedGroup(provider="saml", group_name="IT-Admins"),
        ])
        await self.db.commit()
        result = await list_observed_groups(provider="microsoft", user=ADMIN, db=self.db)
        self.assertEqual([g["group_name"] for g in result["groups"]], ["Vertrieb"])


if __name__ == "__main__":
    unittest.main()
