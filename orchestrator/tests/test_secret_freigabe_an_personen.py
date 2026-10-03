"""Keys an Personen freigeben — Nutzer nur an Personen, nie an Rollen; Admin zentral an Rollen.

Regeln (Vorgabe 03.10.2026):
* Admin und Nutzer legen Keys an. Ein Nutzer gibt seinen Key nur an einzelne Personen
  frei, nie an Rollen. Ein Admin gibt zentral an Rollen frei (Rollenrechte).
* Wer einen Key per Person bekommt, darf ihn eigenen Agenten zuweisen, aber nicht
  aendern, nicht loeschen und nicht weitergeben.
* Wird die Freigabe entzogen, verliert der Empfaenger den Key auch in seinen Agenten.

MC/DC ueber den Zugang (core/secret_zugriff.zugang): admin / eigen / rolle / person / keiner.
"""
import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.api import secrets as api
from app.core import secret_zugriff
from app.models.agent_secret import AgentSecret
from app.models.user import User, UserRole
from tests.test_secrets_gehoeren_ihrem_ersteller import ADMIN, ANDERES, MITGLIED, SecretsTestBasis


class FreigabeAnPersonenTests(SecretsTestBasis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for u in (MITGLIED, ANDERES):
            self.db.add(User(id=u.id, email=u.email, name=u.id.upper(), role=UserRole.MEMBER))
        await self.db.commit()
        self.privat = await self.anlegen(MITGLIED, "Mein-Key")   # gehoert m1

    async def _teilen(self, wer, secret_id, user_ids):
        return await api.set_secret_shares(secret_id, api.SecretShares(user_ids=user_ids),
                                           user=wer, db=self.db, manager=self.manager)

    async def _zugang(self, user, secret_id):
        return await secret_zugriff.zugang(self.db, user, await self.db.get(AgentSecret, secret_id))

    # --- MC/DC: jede Zugangsart einzeln -------------------------------------------
    async def test_admin(self):
        self.assertEqual(await self._zugang(ADMIN, self.privat["id"]), "admin")

    async def test_eigen(self):
        self.assertEqual(await self._zugang(MITGLIED, self.privat["id"]), "eigen")

    async def test_rolle(self):
        self.assertEqual(await self._zugang(MITGLIED, self.firma["id"]), "rolle")

    async def test_person(self):
        await self._teilen(MITGLIED, self.privat["id"], ["m2"])
        self.assertEqual(await self._zugang(ANDERES, self.privat["id"]), "person")

    async def test_keiner(self):
        self.assertIsNone(await self._zugang(ANDERES, self.privat["id"]))
        self.assertIsNone(await self._zugang(ANDERES, self.firma["id"]))

    # --- Freigeben ---------------------------------------------------------------
    async def test_empfaenger_sieht_den_key_mit_herkunft(self):
        await self._teilen(MITGLIED, self.privat["id"], ["m2"])
        liste = (await api.list_secrets(user=ANDERES, db=self.db))["secrets"]
        eintrag = next(x for x in liste if x["id"] == self.privat["id"])
        self.assertEqual((eintrag["zugang"], eintrag["owner_name"], eintrag["manageable"]), ("person", "M1", False))
        self.assertIsNone(eintrag["shared_with_count"], "Empfaenger sehen nicht, an wen sonst freigegeben ist")

    async def test_empfaenger_darf_zuweisen_aber_nicht_aendern_oder_weitergeben(self):
        await self._teilen(MITGLIED, self.privat["id"], ["m2"])
        await api._assert_secret_allowed(self.privat["id"], ANDERES, self.db)   # kein Fehler
        with self.assertRaises(HTTPException) as e:
            await self._teilen(ANDERES, self.privat["id"], ["m1"])
        self.assertEqual(e.exception.status_code, 403)

    async def test_fremde_duerfen_nicht_freigeben(self):
        with self.assertRaises(HTTPException) as e:
            await self._teilen(ANDERES, self.privat["id"], ["m2"])
        self.assertEqual(e.exception.status_code, 403)

    async def test_admin_darf_firmenkey_an_personen_freigeben(self):
        await self._teilen(ADMIN, self.firma["id"], ["m2"])
        self.assertEqual(await self._zugang(ANDERES, self.firma["id"]), "person")

    async def test_unbekannte_person_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as e:
            await self._teilen(MITGLIED, self.privat["id"], ["gibt-es-nicht"])
        self.assertEqual(e.exception.status_code, 422)

    async def test_an_sich_selbst_freigeben_wird_ignoriert(self):
        antwort = await self._teilen(MITGLIED, self.privat["id"], ["m1"])
        self.assertEqual(antwort["shares"], [])

    async def test_freigabe_kennt_keine_rollen(self):
        self.assertEqual(set(api.SecretShares.model_fields), {"user_ids"})

    # --- Entzug ------------------------------------------------------------------
    async def test_entzug_nimmt_den_zugang_und_startet_agenten_neu(self):
        from app.models.agent import Agent
        from app.models.agent_secret import AgentSecretAssignment
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__])
        self.db.add_all([Agent(id="a1", name="A1", user_id="m1"), Agent(id="a2", name="A2", user_id="m2")])
        await self._teilen(MITGLIED, self.privat["id"], ["m2"])
        self.db.add_all([AgentSecretAssignment(agent_id="a1", secret_id=self.privat["id"]),
                         AgentSecretAssignment(agent_id="a2", secret_id=self.privat["id"])])
        await self.db.commit()
        self.assertEqual(await self._zugang(ANDERES, self.privat["id"]), "person")
        api._refresh_agents_for_secret.reset_mock()
        await self._teilen(MITGLIED, self.privat["id"], [])
        self.assertIsNone(await self._zugang(ANDERES, self.privat["id"]))
        # Nur der Agent des Empfaengers wird neu gestartet — der des Besitzers nicht.
        api._refresh_agents_for_secret.assert_awaited_once()
        self.assertEqual(api._refresh_agents_for_secret.await_args.args[3], ["a2"])

    async def test_nutzbare_ids_vereinen_eigen_rolle_person(self):
        fremd = await self.anlegen(ANDERES, "Fremd-Key")
        await self._teilen(ANDERES, fremd["id"], ["m1"])
        ids = await secret_zugriff.nutzbare_secret_ids(self.db, MITGLIED)
        self.assertEqual(ids, {self.privat["id"], self.firma["id"], fremd["id"]})
        self.assertIsNone(await secret_zugriff.nutzbare_secret_ids(self.db, ADMIN))


class EinspielenPrueftDenBesitzerTests(SecretsTestBasis):
    """agent_manager._get_secrets_env: nur Keys, die der Besitzer des Agenten noch nutzen darf."""

    async def test_entzogener_key_wird_nicht_eingespielt(self):
        from app.core.agent_manager import AgentManager
        from app.models.agent import Agent
        from app.models.agent_secret import AgentSecretAssignment
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__])
        self.db.add(Agent(id="a1", name="A1", user_id="m1"))
        privat = await self.anlegen(MITGLIED, "Mein-Key")
        self.db.add(AgentSecretAssignment(agent_id="a1", secret_id=privat["id"]))
        self.db.add(AgentSecretAssignment(agent_id="a1", secret_id=self.firma["id"]))
        await self.db.commit()
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = self.db
        from unittest.mock import patch
        with patch("app.core.agent_manager.decrypt_token", lambda w: w[4:]):
            mgr._nutzbare_secrets_des_besitzers = AsyncMock(return_value={privat["id"], self.firma["id"]})
            self.assertEqual(set(await mgr._get_secrets_env("a1")), {"MEIN_KEY", "FIRMEN_KEY"})
            # Rolle entzieht den Firmen-Key → faellt beim naechsten Start weg
            mgr._nutzbare_secrets_des_besitzers = AsyncMock(return_value={privat["id"]})
            self.assertEqual(set(await mgr._get_secrets_env("a1")), {"MEIN_KEY"})
            # Agent ohne Besitzer / Admin-Besitzer: keine Einschraenkung
            mgr._nutzbare_secrets_des_besitzers = AsyncMock(return_value=None)
            self.assertEqual(set(await mgr._get_secrets_env("a1")), {"MEIN_KEY", "FIRMEN_KEY"})


if __name__ == "__main__":
    unittest.main()


class SicherheitspruefungTests(SecretsTestBasis):
    """Befunde der Sicherheitspruefung vom 03.10.2026 — jeder einzeln festgehalten."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for u in (MITGLIED, ANDERES):
            self.db.add(User(id=u.id, email=u.email, name=u.id.upper(), role=UserRole.MEMBER))
        await self.db.commit()
        self.privat = await self.anlegen(MITGLIED, "Mein-Key")

    async def _teilen(self, wer, secret_id, user_ids):
        return await api.set_secret_shares(secret_id, api.SecretShares(user_ids=user_ids),
                                           user=wer, db=self.db, manager=self.manager)

    async def test_h1_freigegebener_firmenkey_nur_an_eigene_agenten(self):
        from unittest.mock import patch
        await self._teilen(ADMIN, self.firma["id"], ["m2"])
        # a1 ist m2 GETEILT (sichtbar), gehoert aber m1
        with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value={"a1", "a2"})):
            with self.assertRaises(HTTPException) as e:
                await api.assign_secret("a1", self.firma["id"], user=ANDERES, db=self.db, manager=self.manager)
            self.assertEqual(e.exception.status_code, 403)
            # an den eigenen Agenten geht es
            await api.assign_secret("a2", self.firma["id"], user=ANDERES, db=self.db, manager=self.manager)

    async def test_m1_empfaenger_sieht_keine_andeutung_des_werts(self):
        await self._teilen(MITGLIED, self.privat["id"], ["m2"])
        fremd = next(x for x in (await api.list_secrets(user=ANDERES, db=self.db))["secrets"] if x["id"] == self.privat["id"])
        eigen = next(x for x in (await api.list_secrets(user=MITGLIED, db=self.db))["secrets"] if x["id"] == self.privat["id"])
        self.assertIsNone(fremd["masked_value"])
        self.assertIsNotNone(eigen["masked_value"])

    async def test_m4_reservierte_variable_laesst_sich_nicht_freigeben(self):
        proxy = await self.anlegen(MITGLIED, "HTTPS-PROXY")
        with self.assertRaises(HTTPException) as e:
            await self._teilen(MITGLIED, proxy["id"], ["m2"])
        self.assertEqual(e.exception.status_code, 422)
        self.assertTrue(secret_zugriff.variable_reserviert("ANTHROPIC_BASE_URL"))
        self.assertFalse(secret_zugriff.variable_reserviert("ELEVENLABS_API_KEY"))

    async def test_m5_antwort_ohne_email_fuer_nicht_admins(self):
        antwort = await self._teilen(MITGLIED, self.privat["id"], ["m2"])
        self.assertNotIn("email", antwort["shares"][0])

    async def test_m2_besitzer_haengt_ab_auch_ohne_freigabe(self):
        from app.models.agent_secret import AgentSecretAssignment
        self.db.add(AgentSecretAssignment(agent_id="a2", secret_id=self.privat["id"]))
        await self.db.commit()
        # m2 hat keine Freigabe (mehr), darf den Key von seinem Agenten a2 trotzdem abhaengen
        await api.unassign_secret("a2", self.privat["id"], user=ANDERES, db=self.db, manager=self.manager)


class EinspielenSicherheitTests(SecretsTestBasis):
    async def test_l2_agent_mit_verschwundenem_besitzer_bekommt_keine_nutzerkeys(self):
        from app.core.agent_manager import AgentManager
        from app.models.agent import Agent
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__, User.__table__])
        self.db.add(Agent(id="ax", name="X", user_id="geloescht"))
        await self.db.commit()
        mgr = AgentManager.__new__(AgentManager)
        mgr.db = self.db
        self.assertEqual(await mgr._nutzbare_secrets_des_besitzers("ax"), set())


class RollenEntzugTests(SecretsTestBasis):
    async def test_m3_rollenentzug_nimmt_key_aus_den_agenten(self):
        from app.api import roles
        from app.models.agent import Agent
        from app.models.agent_secret import AgentSecretAssignment
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__])
        self.db.add(User(id="m1", email="m1@example.invalid", name="M1", role=UserRole.MEMBER, custom_role_id=7))
        self.db.add(Agent(id="a1", name="A1", user_id="m1"))
        self.db.add(AgentSecretAssignment(agent_id="a1", secret_id=self.firma["id"]))
        await self.db.commit()
        self.freigaben["m1"] = []   # Rolle hat den Firmen-Key verloren
        api._refresh_agents_for_secret.reset_mock()
        await roles._key_entzug_fuer_rolle(self.db, self.manager, 7, {self.firma["id"]})
        from sqlalchemy import select
        rest = (await self.db.execute(select(AgentSecretAssignment))).scalars().all()
        self.assertEqual(rest, [])
        api._refresh_agents_for_secret.assert_awaited_once()


class GegenpruefungTests(SecretsTestBasis):
    async def test_n1_admin_mit_rolle_verliert_nichts(self):
        from app.api import roles
        from app.models.agent import Agent
        from app.models.agent_secret import AgentSecretAssignment
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__])
        self.db.add(User(id="admin", email="a@example.invalid", name="A", role=UserRole.ADMIN, custom_role_id=7))
        self.db.add(Agent(id="ax", name="AX", user_id="admin"))
        self.db.add(AgentSecretAssignment(agent_id="ax", secret_id=self.firma["id"]))
        await self.db.commit()
        await roles._key_entzug_fuer_rolle(self.db, self.manager, 7, {self.firma["id"]})
        from sqlalchemy import select
        self.assertEqual(len((await self.db.execute(select(AgentSecretAssignment))).scalars().all()), 1)

    def test_n3_weitere_reservierte_namen(self):
        for name in ("OPENAI_API_KEY", "GIT_ASKPASS", "NPM_CONFIG_REGISTRY", "BASH_ENV", "DYLD_INSERT_LIBRARIES", "GEMINI_API_KEY"):
            self.assertTrue(secret_zugriff.variable_reserviert(name), name)
