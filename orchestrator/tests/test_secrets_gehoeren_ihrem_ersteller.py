"""Ein Secret gehoert dem, der es anlegt — Nutzen und Verwalten sind getrennt.

28.09.2026, UI-Test mit einem Mitgliedskonto: Das Mitglied legte ein Secret an,
es verschwand sofort aus der Liste und liess sich keinem Agenten zuweisen.
``create_secret`` nahm jeden Aufruf an, ``list_secrets`` und die Zuweisung
liessen Nicht-Admins aber nur Secrets aus der Rollen-Freigabe (``secret_ids``)
sehen — das eigene gehoerte nie dazu. Zweite Luecke: Wem ein Firmen-Secret nur
zur NUTZUNG freigegeben war, durfte es auch aendern und loeschen.

Regeln (MCDC ueber: Admin / Besitzer / per Rolle freigegeben / fremd):

    Aktion        Admin  Besitzer  freigegeben  fremd
    sehen          ja      ja         ja         nein
    zuweisen       ja      ja         ja         nein
    aendern        ja      ja         nein       nein
    loeschen       ja      ja         nein       nein

Was ein Admin anlegt, hat keinen Besitzer: ein Firmen-Secret, das nur ueber die
Rollen-Freigabe an Mitglieder geht (Verhalten wie bisher).
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import secrets as api
from app.models.agent_secret import AgentSecret, AgentSecretAssignment
from app.models.user import UserRole

ADMIN = SimpleNamespace(id="admin", email="admin@example.invalid", role=UserRole.ADMIN)
MITGLIED = SimpleNamespace(id="m1", email="m1@example.invalid", role=UserRole.MEMBER)
ANDERES = SimpleNamespace(id="m2", email="m2@example.invalid", role=UserRole.MEMBER)


class SecretsTestBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                AgentSecret.metadata.create_all,
                tables=[AgentSecret.__table__, AgentSecretAssignment.__table__],
            )
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        # Rollen-Freigaben: m1 darf Secret "firma" nutzen, m2 nichts.
        self.freigaben = {}

        async def perms(user, db):
            return {"secret_ids": self.freigaben.get(user.id)}

        self.patches = [
            patch("app.core.permissions.get_effective_permissions", perms),
            # Agenten gehoeren ihren Nutzern: m1 -> a1, m2 -> a2
            patch("app.core.ownership.visible_agent_ids",
                  AsyncMock(side_effect=lambda user, db: None if user.role == UserRole.ADMIN
                            else {"m1": {"a1"}, "m2": {"a2"}}[user.id])),
            patch.object(api, "_refresh_agents_for_secret", AsyncMock(return_value={})),
            # Besitzer der Agenten: a1 gehoert m1, a2 gehoert m2.
            patch.object(api, "_agent_besitzer",
                         AsyncMock(side_effect=lambda agent_id, db: {"a1": "m1", "a2": "m2"}.get(agent_id))),
            # Verschluesselung ist hier nicht Gegenstand (eigene Tests) — ohne
            # Server-Schluessel greift sie sonst auf /app/data zu.
            patch.object(api, "encrypt_token", lambda wert: f"enc:{wert}"),
            patch.object(api, "decrypt_token", lambda wert: wert[4:]),
        ]
        for p in self.patches:
            p.start()
        self.manager = object()
        self.firma = await self.anlegen(ADMIN, "Firmen-Key")
        self.freigaben["m1"] = [self.firma["id"]]

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.db.close()
        await self.engine.dispose()

    async def anlegen(self, user, name):
        body = api.SecretCreate(name=name, key_name=name.replace("-", "_"), value="wert-123456")
        return await api.create_secret(body, user=user, db=self.db)

    async def sichtbar(self, user):
        return {s["name"] for s in (await api.list_secrets(user=user, db=self.db))["secrets"]}

    async def verboten(self, coro):
        with self.assertRaises(HTTPException) as fehler:
            await coro
        self.assertIn(fehler.exception.status_code, (403, 404))


class SehenTests(SecretsTestBasis):
    async def test_eigenes_secret_bleibt_sichtbar(self):
        """Der gemeldete Fehler: angelegt und sofort verschwunden."""
        await self.anlegen(MITGLIED, "Mein-Key")
        self.assertIn("Mein-Key", await self.sichtbar(MITGLIED))

    async def test_fremdes_eigenes_secret_bleibt_verborgen(self):
        await self.anlegen(MITGLIED, "Mein-Key")
        self.assertNotIn("Mein-Key", await self.sichtbar(ANDERES))

    async def test_freigegebenes_firmen_secret_sichtbar_sonst_nicht(self):
        self.assertIn("Firmen-Key", await self.sichtbar(MITGLIED))
        self.assertNotIn("Firmen-Key", await self.sichtbar(ANDERES))

    async def test_admin_sieht_alles(self):
        await self.anlegen(MITGLIED, "Mein-Key")
        self.assertEqual(await self.sichtbar(ADMIN), {"Firmen-Key", "Mein-Key"})


class ZuweisenTests(SecretsTestBasis):
    async def test_besitzer_weist_eigenem_agenten_zu(self):
        eigen = await self.anlegen(MITGLIED, "Mein-Key")
        antwort = await api.assign_secret("a1", eigen["id"], user=MITGLIED, db=self.db, manager=self.manager)
        self.assertTrue(antwort["ok"])

    async def test_freigegebenes_darf_zugewiesen_werden(self):
        antwort = await api.assign_secret("a1", self.firma["id"], user=MITGLIED, db=self.db, manager=self.manager)
        self.assertTrue(antwort["ok"])

    async def test_fremdes_secret_nicht_zuweisbar(self):
        eigen = await self.anlegen(MITGLIED, "Mein-Key")
        await self.verboten(api.assign_secret("a2", eigen["id"], user=ANDERES, db=self.db, manager=self.manager))

    async def test_nicht_an_fremden_agenten(self):
        eigen = await self.anlegen(MITGLIED, "Mein-Key")
        await self.verboten(api.assign_secret("a2", eigen["id"], user=MITGLIED, db=self.db, manager=self.manager))


class GeteilterAgentTests(SecretsTestBasis):
    """Sicherheits-Review 28.09.2026: ein NUR GETEILTER Agent (m2 sieht a1 von m1)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # m2 hat a1 geteilt bekommen; Besitzer von a1 ist m1.
        self.patches.append(patch("app.core.ownership.visible_agent_ids",
                                  AsyncMock(side_effect=lambda user, db: None if user.role == UserRole.ADMIN
                                            else {"m1": {"a1"}, "m2": {"a1", "a2"}}[user.id])))
        self.patches.append(patch.object(api, "_agent_besitzer",
                                         AsyncMock(side_effect=lambda agent_id, db: {"a1": "m1", "a2": "m2"}.get(agent_id))))
        for p in self.patches[-2:]:
            p.start()

    async def test_geteilter_agent_zeigt_keine_unfreigegebenen_secrets(self):
        """Vorher: alle Secrets des Agenten samt Teil des Werts, ohne Freigabe."""
        await api.assign_secret("a1", self.firma["id"], user=ADMIN, db=self.db, manager=self.manager)
        antwort = await api.get_agent_secrets("a1", user=ANDERES, db=self.db)
        self.assertEqual(antwort["secrets"], [])

    async def test_besitzer_sieht_die_secrets_seines_agenten(self):
        await api.assign_secret("a1", self.firma["id"], user=ADMIN, db=self.db, manager=self.manager)
        antwort = await api.get_agent_secrets("a1", user=MITGLIED, db=self.db)
        self.assertEqual([s["name"] for s in antwort["secrets"]], ["Firmen-Key"])

    async def test_eigenes_secret_nicht_an_nur_geteilten_agenten(self):
        """Sonst liegt der Wert im Container eines anderen Nutzers."""
        eigen = await self.anlegen(ANDERES, "Privat-Key")
        await self.verboten(api.assign_secret("a1", eigen["id"], user=ANDERES, db=self.db, manager=self.manager))
        antwort = await api.assign_secret("a2", eigen["id"], user=ANDERES, db=self.db, manager=self.manager)
        self.assertTrue(antwort["ok"])


class VerwaltenTests(SecretsTestBasis):
    async def test_besitzer_aendert_und_loescht(self):
        eigen = await self.anlegen(MITGLIED, "Mein-Key")
        neu = await api.update_secret(eigen["id"], api.SecretUpdate(name="Umbenannt"),
                                      user=MITGLIED, db=self.db, manager=self.manager)
        self.assertEqual(neu["name"], "Umbenannt")
        await api.delete_secret(eigen["id"], user=MITGLIED, db=self.db, manager=self.manager)
        self.assertNotIn("Umbenannt", await self.sichtbar(ADMIN))

    async def test_freigabe_zum_nutzen_ist_keine_freigabe_zum_aendern(self):
        """Die zweite Luecke: nutzen durfte auch aendern und loeschen."""
        await self.verboten(api.update_secret(self.firma["id"], api.SecretUpdate(value="gekapert"),
                                              user=MITGLIED, db=self.db, manager=self.manager))
        await self.verboten(api.delete_secret(self.firma["id"], user=MITGLIED, db=self.db, manager=self.manager))

    async def test_fremdes_eigenes_nicht_aenderbar(self):
        eigen = await self.anlegen(MITGLIED, "Mein-Key")
        await self.verboten(api.update_secret(eigen["id"], api.SecretUpdate(name="x"),
                                              user=ANDERES, db=self.db, manager=self.manager))
        await self.verboten(api.delete_secret(eigen["id"], user=ANDERES, db=self.db, manager=self.manager))

    async def test_admin_verwaltet_auch_fremde(self):
        eigen = await self.anlegen(MITGLIED, "Mein-Key")
        await api.delete_secret(eigen["id"], user=ADMIN, db=self.db, manager=self.manager)
        self.assertNotIn("Mein-Key", await self.sichtbar(ADMIN))

    async def test_admin_secret_hat_keinen_besitzer(self):
        self.assertIsNone((await self.db.get(AgentSecret, self.firma["id"])).owner_id)


if __name__ == "__main__":
    unittest.main()
