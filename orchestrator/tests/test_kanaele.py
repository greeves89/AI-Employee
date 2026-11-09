"""Kanal-Einstellungen (core/kanaele, API /agents/{id}/kanaele) — 1.448.0.

Bis 1.447 ließen sich Teams, Slack, Discord und WhatsApp nur direkt in der Datenbank
einstellen. Festgehalten wird:

* Die neutralen Felder landen genau unter den Schlüsseln, die die Gateways lesen
  (services/*_gateway.py) — geprüft über deren eigene ``channel_config``/``is_enabled``.
* Zugangsdaten werden verschlüsselt gespeichert und nie zurückgegeben; leer entfernt sie.
* Ungültiges wird abgelehnt (Kennungen, Längen, Nummern), Fehlendes bleibt unverändert.
* Teams: Chats aus Besprechungen bleiben, wenn man die eigene Auswahl ändert.
* Ändern nur Besitzer/Admin; die Chat-Auswahl ohne eigenes Konto nur der Besitzer.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "c2VjcmV0LWtleS1mb3ItdGVzdHMtb25seS0zMmJ5dGU=")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.core import kanaele  # noqa: E402
from app.core.encryption import decrypt_token  # noqa: E402
from app.models.agent import Agent, AgentState  # noqa: E402
from app.models.agent_access import AgentAccess  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.oauth_integration import OAuthIntegration  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


def _agent(config):
    return SimpleNamespace(config=config, name="Assistenz", id="a1")


class AbbildungTests(unittest.TestCase):
    """Die Felder landen dort, wo die Gateways lesen — mit deren eigenen Funktionen geprüft."""

    def test_slack(self):
        from app.services import slack_gateway

        cfg, geaendert = kanaele.setzen({}, "slack", {"aktiv": True, "ziele": ["C0123", "C0123", " "],
                                                      "nur_bei_erwaehnung": False, "stichworte": ["Hilfe", "hilfe"],
                                                      "bot_kennung": "U999", "token": "xoxb-geheim"})
        roh = slack_gateway.channel_config(_agent(cfg))
        self.assertTrue(slack_gateway.is_enabled(_agent(cfg)))
        self.assertEqual(roh["channels"], ["C0123"])
        self.assertFalse(roh["mention_only"])
        self.assertEqual(roh["mention_names"], ["Hilfe"])
        self.assertEqual(roh["bot_user_id"], "U999")
        self.assertEqual(decrypt_token(roh["bot_token_enc"]), "xoxb-geheim")
        self.assertIn("token gesetzt", geaendert)

    def test_discord(self):
        from app.services import discord_gateway

        cfg, _ = kanaele.setzen({}, "discord", {"aktiv": True, "ziele": ["123456789"], "nur_bei_erwaehnung": True,
                                                "stichworte": ["bot"], "token": "discord-geheim"})
        roh = discord_gateway.channel_config(_agent(cfg))
        self.assertTrue(discord_gateway.is_enabled(_agent(cfg)))
        self.assertTrue(roh["mention_required"])
        self.assertEqual(roh["keywords"], ["bot"])
        self.assertEqual(decrypt_token(roh["bot_token_enc"]), "discord-geheim")

    def test_whatsapp(self):
        from app.services import whatsapp_gateway

        cfg, _ = kanaele.setzen({}, "whatsapp", {"aktiv": True, "telefon_id": "10987654321",
                                                 "absender": ["+49 151 2345678"], "token": "EAAB"})
        roh = whatsapp_gateway.channel_config(_agent(cfg))
        self.assertTrue(whatsapp_gateway.is_enabled(_agent(cfg)))
        self.assertEqual(roh["allowed_senders"], ["+491512345678"])
        self.assertEqual(decrypt_token(roh["access_token_enc"]), "EAAB")

    def test_teams_behaelt_besprechungs_chats(self):
        from app.services import teams_gateway

        alt = {"channels": {"teams": {"chat_ids": ["19:meet", "19:alt"], "chat_ids_meetings": ["19:meet"]}}}
        self.assertEqual(kanaele.lesen(alt, "teams")["ziele"], ["19:alt"])
        cfg, _ = kanaele.setzen(alt, "teams", {"aktiv": True, "ziele": ["19:neu@thread.v2"],
                                               "teams_kanaele": [{"team_id": "t1", "channel_id": "19:k@thread.tacv2"}]})
        roh = teams_gateway.channel_config(_agent(cfg))
        self.assertEqual(roh["chat_ids"], ["19:meet", "19:neu@thread.v2"])
        self.assertEqual(roh["channels"], [{"team_id": "t1", "channel_id": "19:k@thread.tacv2"}])
        self.assertTrue(teams_gateway.is_enabled(_agent(cfg)))
        self.assertEqual(kanaele.lesen(cfg, "teams")["besprechungs_chats"], 1)

    def test_lesen_gibt_nie_ein_token_zurueck(self):
        cfg, _ = kanaele.setzen({}, "slack", {"token": "xoxb-geheim"})
        stand = kanaele.lesen(cfg, "slack")
        self.assertTrue(stand["token_gesetzt"])
        self.assertNotIn("xoxb", str(stand))
        self.assertNotIn(cfg["channels"]["slack"]["bot_token_enc"], str(stand))

    def test_fehlendes_bleibt_leeres_token_entfernt(self):
        cfg, _ = kanaele.setzen({}, "discord", {"ziele": ["1"], "token": "t"})
        cfg, geaendert = kanaele.setzen(cfg, "discord", {"aktiv": True})
        self.assertEqual(cfg["channels"]["discord"]["channels"], ["1"])
        self.assertIn("bot_token_enc", cfg["channels"]["discord"])
        cfg, geaendert = kanaele.setzen(cfg, "discord", {"token": ""})
        self.assertNotIn("bot_token_enc", cfg["channels"]["discord"])
        self.assertEqual(geaendert, ["token entfernt"])

    def test_ungueltiges_wird_abgelehnt(self):
        faelle = [
            ("slack", {"ziele": ["C1; DROP"]}),
            ("slack", {"ziele": ["C"] * 51}),
            ("discord", {"stichworte": ["x" * 61]}),
            ("whatsapp", {"absender": ["abc"]}),
            ("whatsapp", {"telefon_id": "1/2"}),
            ("teams", {"teams_kanaele": [{"team_id": "t"}]}),
            ("slack", {"token": "x" * 1001}),
            ("telegram", {"aktiv": True}),
        ]
        for kanal, e in faelle:
            with self.assertRaises(kanaele.UngueltigeEinstellung, msg=(kanal, e)):
                kanaele.setzen({}, kanal, e)

    def test_andere_einstellungen_bleiben(self):
        cfg, _ = kanaele.setzen({"msgraph_access": "write", "channels": {"slack": {"channels": ["C1"]}}},
                                "discord", {"aktiv": True})
        self.assertEqual(cfg["msgraph_access"], "write")
        self.assertEqual(cfg["channels"]["slack"]["channels"], ["C1"])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent, AgentAccess, AuditLog, OAuthIntegration):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u1", email="besitzer@example.com", name="Besitzer", role=UserRole.MEMBER),
                User(id="u2", email="kollege@example.com", name="Kollege", role=UserRole.MEMBER),
                User(id="adm", email="admin@example.com", name="Admin", role=UserRole.ADMIN),
                Agent(id="a1", name="Assistenz", state=AgentState.RUNNING, user_id="u1",
                      config={"integrations": ["microsoft"]}),
                AgentAccess(agent_id="a1", user_id="u2"),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _als(self, uid, fn, *args):
        async with self.Session() as db:
            return await fn(*args, user=await db.get(User, uid), db=db)

    async def test_besitzer_setzt_protokoll_ohne_token(self):
        from app.api import agents as api

        stand = await self._als("u1", api.update_agent_kanal, "a1", "slack",
                                api.KanalUpdate(aktiv=True, ziele=["C1"], token="xoxb-geheim"))
        self.assertTrue(stand["token_gesetzt"])
        async with self.Session() as db:
            prot = (await db.execute(select(AuditLog))).scalars().all()
        self.assertEqual(len(prot), 1)
        self.assertNotIn("xoxb", f"{prot[0].command} {prot[0].meta}")
        alle = await self._als("u2", api.get_agent_kanaele, "a1")
        self.assertNotIn("xoxb", str(alle))

    async def test_freigegebener_liest_aendert_aber_nicht(self):
        from app.api import agents as api

        with self.assertRaises(HTTPException) as e:
            await self._als("u2", api.update_agent_kanal, "a1", "slack", api.KanalUpdate(aktiv=True))
        self.assertEqual(e.exception.status_code, 403)
        await self._als("adm", api.update_agent_kanal, "a1", "slack", api.KanalUpdate(aktiv=True))

    async def test_ungueltig_ist_422(self):
        from app.api import agents as api

        with self.assertRaises(HTTPException) as e:
            await self._als("u1", api.update_agent_kanal, "a1", "slack", api.KanalUpdate(ziele=["a b"]))
        self.assertEqual(e.exception.status_code, 422)

    async def test_chat_auswahl_ohne_eigenes_konto_nur_fuer_den_besitzer(self):
        from app.api import agents as api

        with self.assertRaises(HTTPException) as e:
            await self._als("adm", api.get_agent_teams_auswahl, "a1")
        self.assertEqual(e.exception.status_code, 403, "Admin sieht die Chats des Besitzers nicht")

        async def graph(method, path, token, **kw):
            if path.startswith("/me/chats"):
                return {"value": [{"id": "19:c", "chatType": "group", "topic": None,
                                   "members": [{"displayName": "Kundin A"}, {"displayName": "Besitzer"}]}]}
            if path.startswith("/me/joinedTeams"):
                return {"value": [{"id": "t1", "displayName": "Vertrieb"}]}
            return {"value": [{"id": "19:k", "displayName": "Allgemein"}]}

        with patch("app.core.agenten_konto.token_fuer", AsyncMock(return_value="tok")), \
                patch("app.core.msgraph_mcp._graph", graph):
            erg = await self._als("u1", api.get_agent_teams_auswahl, "a1")
        self.assertEqual(erg["chats"][0]["name"], "Kundin A, Besitzer")
        self.assertEqual(erg["kanaele"][0]["name"], "Vertrieb › Allgemein")

    async def test_chat_auswahl_ohne_anbindung_409(self):
        from app.api import agents as api

        async with self.Session() as db:
            a = await db.get(Agent, "a1")
            a.config = {}
            await db.commit()
        with self.assertRaises(HTTPException) as e:
            await self._als("u1", api.get_agent_teams_auswahl, "a1")
        self.assertEqual(e.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
