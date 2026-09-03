"""``resolve_target`` / ``record_observed_groups`` — Kern der Gruppen-Rollen-Zuordnung.

Ersetzt ``saml_config.role_for_groups`` (nur SAML, nur 3 Enum-Rollen, JSON-Blob in
den Einstellungen) durch eine Tabelle, die beide Anmeldewege (SAML, Microsoft-OIDC)
und beide Zielarten (Enum-Rolle, CustomRole) bedient. Die alten Garantien bleiben
Pflicht: kein Treffer aendert nichts, die hoechste (jetzt: explizit priorisierte)
Zuordnung gewinnt.
"""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.sso_group_roles import record_observed_groups, resolve_target
from app.models.base import Base
from app.models.sso_group_mapping import SsoGroupRoleMapping
from app.models.sso_observed_group import SsoObservedGroup


class SsoGroupRolesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c, tables=[SsoGroupRoleMapping.__table__, SsoObservedGroup.__table__]
                )
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _mapping(self, **over):
        data = dict(provider="microsoft", group_name="Vertrieb", target_kind="role",
                    target_value="member", priority=0)
        data.update(over)
        self.db.add(SsoGroupRoleMapping(**data))
        await self.db.commit()

    async def test_no_mapping_no_target(self):
        self.assertIsNone(await resolve_target(self.db, "microsoft", ["Vertrieb"]))

    async def test_no_groups_no_target(self):
        await self._mapping()
        self.assertIsNone(await resolve_target(self.db, "microsoft", []))

    async def test_matching_group_resolves(self):
        await self._mapping()
        self.assertEqual(
            await resolve_target(self.db, "microsoft", ["Vertrieb"]),
            ("role", "member"),
        )

    async def test_match_is_case_insensitive(self):
        await self._mapping(group_name="IT-Admins", target_value="admin")
        self.assertEqual(
            await resolve_target(self.db, "microsoft", ["it-admins"]),
            ("role", "admin"),
        )

    async def test_non_matching_group_no_target(self):
        await self._mapping()
        self.assertIsNone(await resolve_target(self.db, "microsoft", ["Marketing"]))

    async def test_provider_isolation(self):
        # Dieselbe Gruppe bei SAML zugeordnet darf am Microsoft-Login nichts aendern
        # — beide IdPs haben eigene, unabhaengige Gruppennamensraeume.
        await self._mapping(provider="saml", group_name="Vertrieb", target_value="admin")
        self.assertIsNone(await resolve_target(self.db, "microsoft", ["Vertrieb"]))

    async def test_highest_priority_wins_on_multiple_matches(self):
        await self._mapping(group_name="Alle", target_value="member", priority=1)
        await self._mapping(group_name="IT-Admins", target_value="admin", priority=5)
        target = await resolve_target(self.db, "microsoft", ["Alle", "IT-Admins"])
        self.assertEqual(target, ("role", "admin"))

    async def test_custom_role_target_kind_is_passed_through(self):
        await self._mapping(group_name="Vertrieb", target_kind="custom_role", target_value="12")
        self.assertEqual(
            await resolve_target(self.db, "microsoft", ["Vertrieb"]),
            ("custom_role", "12"),
        )


class RecordObservedGroupsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(c, tables=[SsoObservedGroup.__table__])
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _all(self):
        from sqlalchemy import select
        return (await self.db.execute(select(SsoObservedGroup))).scalars().all()

    async def test_new_group_is_recorded(self):
        await record_observed_groups(self.db, "microsoft", ["Vertrieb"])
        await self.db.commit()
        rows = await self._all()
        self.assertEqual([r.group_name for r in rows], ["Vertrieb"])

    async def test_seeing_the_same_group_again_does_not_duplicate(self):
        await record_observed_groups(self.db, "microsoft", ["Vertrieb"])
        await self.db.commit()
        await record_observed_groups(self.db, "microsoft", ["Vertrieb"])
        await self.db.commit()
        rows = await self._all()
        self.assertEqual(len(rows), 1)

    async def test_empty_groups_records_nothing(self):
        await record_observed_groups(self.db, "microsoft", [])
        await self.db.commit()
        self.assertEqual(await self._all(), [])

    async def test_never_raises_on_a_broken_session(self):
        # Ein Login darf nicht scheitern, nur weil die Beobachtungstabelle klemmt.
        await self.db.close()
        await record_observed_groups(self.db, "microsoft", ["Vertrieb"])  # kein Raise

    async def test_growth_is_capped_per_provider(self):
        """Ohne Deckel waechst die Tabelle mit jedem je gesehenen Gruppennamen aus
        einer Quelle, die wir nicht kontrollieren (der IdP), unbegrenzt weiter —
        Security-Review 2026-08-13."""
        import app.core.sso_group_roles as mod

        original = mod._MAX_OBSERVED_GROUPS_PER_PROVIDER
        mod._MAX_OBSERVED_GROUPS_PER_PROVIDER = 2
        try:
            await record_observed_groups(self.db, "microsoft", ["A", "B", "C"])
            await self.db.commit()
            rows = await self._all()
            self.assertEqual(len(rows), 2)
        finally:
            mod._MAX_OBSERVED_GROUPS_PER_PROVIDER = original

    async def test_updating_known_groups_still_works_once_the_cap_is_reached(self):
        """Der Deckel darf nur NEUE Namen bremsen — schon bekannte Gruppen muessen
        weiterhin ihren last_seen_at-Stempel bekommen, sonst veraltet die Anzeige
        in der Verwaltung fuer laengst etablierte Gruppen."""
        import app.core.sso_group_roles as mod

        original = mod._MAX_OBSERVED_GROUPS_PER_PROVIDER
        mod._MAX_OBSERVED_GROUPS_PER_PROVIDER = 1
        try:
            await record_observed_groups(self.db, "microsoft", ["A"])
            await self.db.commit()
            await record_observed_groups(self.db, "microsoft", ["A", "B"])
            await self.db.commit()
            rows = await self._all()
            # "A" bleibt (aktualisiert), "B" kam nicht mehr durch — der Deckel steht.
            self.assertEqual([r.group_name for r in rows], ["A"])
        finally:
            mod._MAX_OBSERVED_GROUPS_PER_PROVIDER = original

    async def test_query_is_scoped_to_the_current_batch_not_the_whole_table(self):
        """Frueher wurde bei JEDEM Login die komplette Tabelle geladen — die Kosten
        eines Logins waeren mit der Tabellengroesse gewachsen, nicht mit der Anzahl
        Gruppen DIESES Logins. Hier indirekt geprueft: eine bereits vorhandene,
        im aktuellen Login nicht genannte Gruppe bleibt unangetastet (kein
        "alles einlesen und neu schreiben")."""
        await record_observed_groups(self.db, "microsoft", ["A"])
        await self.db.commit()
        [row_a] = await self._all()
        stamp_before = row_a.last_seen_at

        await record_observed_groups(self.db, "microsoft", ["B"])
        await self.db.commit()

        rows = {r.group_name: r for r in await self._all()}
        self.assertEqual(rows["A"].last_seen_at, stamp_before)
        self.assertIn("B", rows)


if __name__ == "__main__":
    unittest.main()
