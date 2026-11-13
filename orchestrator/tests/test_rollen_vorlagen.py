"""Rollen-Vorlagen und Rechte-Übersicht (Enterprise-Rollenmodell, core/rollen_vorlagen).

Eine Vorlage füllt nur eine normale Gruppe (CustomRole) mit Rechten, die core/permissions schon
durchsetzt — kein zweites Rechtesystem. Geprüft wird deshalb auch, dass eine Gruppe aus der
Vorlage am Nutzer wirklich wirkt.
"""

import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import roles as api
from app.core import nutzung, rollen_vorlagen
from app.core.permissions import _merge_defaults, get_effective_permissions
from app.models.audit_log import AuditLog
from app.models.custom_role import CustomRole
from app.models.user import User, UserRole

ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)
MITGLIED = SimpleNamespace(id="m1", role=UserRole.MEMBER)


class VorlagenTests(unittest.TestCase):
    def test_nur_bekannte_rechte_und_gueltige_werte(self):
        bekannt = set(_merge_defaults({})) | {"max_autonomy_level"}
        for schluessel, v in rollen_vorlagen.VORLAGEN.items():
            self.assertLessEqual(set(v["rechte"]), bekannt, schluessel)
            self.assertIn(v["rechte"].get("max_autonomy_level"), ("l1", "l2", "l3", "l4"), schluessel)
            self.assertIn(v["grundrolle"], {r.value for r in UserRole}, schluessel)
            for pfad in v["rechte"].get("menu_paths") or []:
                self.assertIn(pfad.strip("/").split("/")[0], nutzung.BEREICHE, (schluessel, pfad))

    def test_nie_unbeabsichtigt_weiter_als_die_grundrolle(self):
        """Was eine Vorlage NICHT ausdrücklich setzt, ist genau der Wert ihrer Grundrolle —
        ein fehlender Schlüssel hieße in einer eigenen Rolle „ohne Grenze“. Ausdrücklich
        gesetzte Werte sind die bewusste Aussage der Vorlage (z. B. Entwickler: L4, 10 Agenten)."""
        from app.core.permissions import DEFAULT_PERMISSIONS_BY_ROLE

        for schluessel, v in rollen_vorlagen.VORLAGEN.items():
            basis = DEFAULT_PERMISSIONS_BY_ROLE[UserRole(v["grundrolle"])]
            rechte = rollen_vorlagen.rechte_fuer(schluessel)
            self.assertEqual(set(rechte), set(basis), schluessel)
            for k in set(basis) - set(v["rechte"]):
                self.assertEqual(rechte[k], basis[k], (schluessel, k))
        # Für Betrachter und Gast: keine Liste der Grundrolle wird zu „alle“.
        for schluessel in ("betrachter", "gast"):
            rechte = rollen_vorlagen.rechte_fuer(schluessel)
            for k in ("template_ids", "models", "llm_providers", "url_host_patterns", "search_indexes",
                      "integration_providers", "mcp_server_ids", "secret_ids", "ai_account_ids"):
                if DEFAULT_PERMISSIONS_BY_ROLE[UserRole(rollen_vorlagen.VORLAGEN[schluessel]["grundrolle"])][k] is not None \
                        or schluessel == "gast" and k in rollen_vorlagen.VORLAGEN["gast"]["rechte"]:
                    self.assertIsNotNone(rechte[k], (schluessel, k))

    def test_liste_erklaert_plattform_admin(self):
        namen = [v["name"] for v in rollen_vorlagen.liste()]
        self.assertEqual(namen[0], "Plattform-Admin")
        self.assertIn("Mitarbeiter", namen)

    def test_rechte_kurz(self):
        kurz = rollen_vorlagen.rechte_kurz(_merge_defaults({"max_agents": 3, "max_autonomy_level": "l3",
                                                            "models": ["a", "b"]}))
        self.assertEqual((kurz["agenten"], kurz["autonomie"], kurz["modelle"]), (3, "L3", 2))
        self.assertEqual(kurz["secrets"], "keine", "Secrets: ohne Freigabe keine (Standard-Sperre)")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for m in (User, CustomRole, AuditLog):
                await conn.run_sync(m.metadata.create_all, tables=[m.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add_all([
            User(id="adm", email="admin@example.com", name="Admin", role=UserRole.ADMIN),
            User(id="m1", email="gast@example.com", name="Gast", role=UserRole.MEMBER),
        ])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_gruppe_aus_vorlage_wirkt_am_nutzer(self):
        r = await api.gruppe_aus_vorlage("gast", None, user=ADMIN, db=self.db)
        self.assertEqual((r["name"], r["grundrolle"]), ("Gast", "member"))
        nutzer = await self.db.get(User, "m1")
        nutzer.custom_role_id = r["id"]
        await self.db.commit()
        rechte = await get_effective_permissions(nutzer, self.db)
        self.assertEqual((rechte["max_agents"], rechte["max_autonomy_level"], rechte["mcp_server_ids"]), (0, "l1", []))
        protokoll = (await self.db.execute(select(AuditLog))).scalars().all()
        self.assertTrue(any("Vorlage „Gast“" in (a.command or "") for a in protokoll))

    async def test_name_doppelt_unbekannt_und_nur_admin(self):
        await api.gruppe_aus_vorlage("mitarbeiter", None, user=ADMIN, db=self.db)
        for aufruf, code in ((lambda: api.gruppe_aus_vorlage("mitarbeiter", None, user=ADMIN, db=self.db), 409),
                             (lambda: api.gruppe_aus_vorlage("gibtsnicht", None, user=ADMIN, db=self.db), 404),
                             (lambda: api.gruppe_aus_vorlage("gast", None, user=MITGLIED, db=self.db), 403)):
            with self.assertRaises(HTTPException) as e:
                await aufruf()
            self.assertEqual(e.exception.status_code, code)
        zweite = await api.gruppe_aus_vorlage("mitarbeiter", {"name": "Mitarbeiter Vertrieb"}, user=ADMIN, db=self.db)
        self.assertEqual(zweite["name"], "Mitarbeiter Vertrieb")

    async def test_uebersicht(self):
        r = await api.gruppe_aus_vorlage("gast", None, user=ADMIN, db=self.db)
        (await self.db.get(User, "m1")).custom_role_id = r["id"]
        await self.db.commit()
        zeilen = {z["id"]: z for z in (await api.rechte_uebersicht(user=ADMIN, db=self.db))["nutzer"]}
        self.assertEqual(zeilen["adm"]["rechte"], {"alles": True})
        self.assertEqual((zeilen["m1"]["gruppe"], zeilen["m1"]["anmeldung"], zeilen["m1"]["rechte"]["agenten"]),
                         ("Gast", "lokal", 0))
        with self.assertRaises(HTTPException):
            await api.rechte_uebersicht(user=MITGLIED, db=self.db)


if __name__ == "__main__":
    unittest.main()
