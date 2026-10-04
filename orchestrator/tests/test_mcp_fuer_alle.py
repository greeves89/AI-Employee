"""MCP-Server nicht automatisch fuer alle Nutzer (#909).

Befund (Markttest): Alle anlagenweiten MCP-Server — auch per OAuth mit dem
Admin-Konto verbundene — hingen an den Agenten JEDES Nutzers, und Mitglieder sahen
die Server-URLs. Ein OAuth-Server arbeitet mit dem Konto des Admins, also handelte
jeder Agent dort als Admin.

Regel (core/mcp_zugriff.py), MC/DC ueber Admin / Rollenliste / fuer_alle:

    Nutzer                     Server fuer_alle   Server nicht fuer_alle
    Admin                      ja                 ja
    Rolle mit Liste [X]        nur wenn X         nur wenn X
    ohne Liste                 ja                 nein

Geprueft wird, was der Nutzer am Ende sieht und bekommt: Liste, Server des Agenten,
Umgebung des Containers, Werkzeuge der Sprachfront, Zuweisung.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import mcp_zugriff
from app.core.agent_mcp_servers import servers_for_agent, voice_toolspecs
from app.models.agent import Agent, AgentState
from app.models.custom_role import CustomRole
from app.models.mcp_server import McpServer
from app.models.user import User, UserRole


def _werkzeug(name):
    return {"name": name, "description": name, "inputSchema": {"type": "object", "properties": {}}}


class McpFuerAlleBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                McpServer.metadata.create_all,
                tables=[McpServer.__table__, User.__table__, CustomRole.__table__, Agent.__table__],
            )
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.rolle = CustomRole(id=7, name="Vertrieb", permissions={"mcp_server_ids": []})
        self.db.add(self.rolle)
        await self.db.flush()
        self.admin = User(id="admin", email="admin@example.invalid", name="Admin", role=UserRole.ADMIN)
        self.mitglied = User(id="m1", email="m1@example.invalid", name="M1", role=UserRole.MEMBER)
        self.mit_rolle = User(id="m2", email="m2@example.invalid", name="M2", role=UserRole.MEMBER,
                              custom_role_id=7)
        self.db.add_all([self.admin, self.mitglied, self.mit_rolle])
        # Bestand (bei der Umstellung auf fuer_alle gesetzt) und ein neuer Server.
        self.alt = McpServer(id=1, name="bestand", url="https://bestand.example.invalid/mcp",
                             tools=[_werkzeug("alt_tool")], enabled=True, fuer_alle=True,
                             auth_token_encrypted="tok-alt")
        self.neu = McpServer(id=2, name="neu", url="https://neu.example.invalid/mcp",
                             tools=[_werkzeug("neu_tool")], enabled=True,
                             auth_token_encrypted="tok-neu", oauth_enabled=True)
        self.db.add_all([self.alt, self.neu])
        self.db.add_all([
            Agent(id="a-admin", name="A", user_id="admin", state=AgentState.RUNNING, config={}),
            Agent(id="a-m1", name="B", user_id="m1", state=AgentState.RUNNING, config={}),
            Agent(id="a-m2", name="C", user_id="m2", state=AgentState.RUNNING, config={}),
        ])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _rollenliste(self, ids):
        self.rolle.permissions = {"mcp_server_ids": ids}
        await self.db.commit()


class NeuerServerIstAusTests(McpFuerAlleBasis):
    async def test_neuer_server_ist_nicht_fuer_alle(self):
        self.assertFalse(self.neu.fuer_alle)

    async def test_anlegen_ohne_angabe_ist_aus(self):
        from app.api.mcp_servers import McpServerCreate
        self.assertFalse(McpServerCreate(name="x", url="https://example.invalid/mcp").fuer_alle)


class NutzbareIdsTests(McpFuerAlleBasis):
    async def test_admin_alle(self):
        self.assertIsNone(await mcp_zugriff.nutzbare_mcp_server_ids(self.db, self.admin))

    async def test_ohne_liste_nur_fuer_alle(self):
        self.assertEqual(await mcp_zugriff.nutzbare_mcp_server_ids(self.db, self.mitglied), {1})

    async def test_schalter_an_macht_ihn_nutzbar(self):
        self.neu.fuer_alle = True
        await self.db.commit()
        self.assertEqual(await mcp_zugriff.nutzbare_mcp_server_ids(self.db, self.mitglied), {1, 2})

    async def test_rollenliste_gewinnt_gegen_fuer_alle(self):
        await self._rollenliste([2])
        self.assertEqual(await mcp_zugriff.nutzbare_mcp_server_ids(self.db, self.mit_rolle), {2})

    async def test_leere_rollenliste_heisst_keiner(self):
        self.assertEqual(await mcp_zugriff.nutzbare_mcp_server_ids(self.db, self.mit_rolle), set())


class ServerDesAgentenTests(McpFuerAlleBasis):
    async def _namen(self, agent_id, config=None):
        return {s.name for s in await servers_for_agent(self.db, agent_id, config)}

    async def test_mitglied_bekommt_neuen_server_nicht(self):
        self.assertEqual(await self._namen("a-m1"), {"bestand"})

    async def test_mitglied_bekommt_ihn_auch_nicht_durch_ausdrueckliche_wahl(self):
        self.assertEqual(await self._namen("a-m1", {"mcp_servers": [1, 2]}), {"bestand"})

    async def test_mit_fuer_alle_schon(self):
        self.neu.fuer_alle = True
        await self.db.commit()
        self.assertEqual(await self._namen("a-m1"), {"bestand", "neu"})

    async def test_rollenliste_gewinnt(self):
        await self._rollenliste([2])
        self.assertEqual(await self._namen("a-m2"), {"neu"})

    async def test_admin_bekommt_alle(self):
        self.assertEqual(await self._namen("a-admin"), {"bestand", "neu"})

    async def test_agent_ohne_besitzer_nur_fuer_alle(self):
        self.db.add(Agent(id="a-los", name="D", user_id=None, state=AgentState.RUNNING, config={}))
        await self.db.commit()
        self.assertEqual(await self._namen("a-los"), {"bestand"})

    async def test_rechtefehler_gibt_keine_server_frei(self):
        with patch("app.core.mcp_zugriff.nutzbare_mcp_server_ids", AsyncMock(side_effect=RuntimeError("weg"))):
            self.assertEqual(await self._namen("a-m1"), set())

    async def test_sprachfront_bekommt_die_werkzeuge_nicht(self):
        """Die Sprachfront baut ihre Werkzeuge aus servers_for_agent (realtime_voice_session)."""
        with patch("app.core.agent_mcp_servers.decrypt_token", lambda w: w):
            _, plan, _ = voice_toolspecs(await servers_for_agent(self.db, "a-m1", {}))
        self.assertIn("alt_tool", plan)
        self.assertNotIn("neu_tool", plan)


class ContainerUmgebungTests(McpFuerAlleBasis):
    async def _env(self, agent_id):
        from app.core.agent_manager import AgentManager
        manager = AgentManager(self.db, None, None)
        with patch("app.core.agent_manager.decrypt_token", lambda w: w), \
             patch("app.services.mcp_oauth_refresh.refresh_if_needed", AsyncMock()):
            return await manager._get_custom_mcp_env(agent_config={}, agent_id=agent_id)

    async def test_mitglied_ohne_rolle_bekommt_weder_url_noch_token(self):
        env = await self._env("a-m1")
        self.assertEqual(set(json.loads(env["CUSTOM_MCP_SERVERS"])), {"bestand"})
        self.assertNotIn("neu", json.loads(env["CUSTOM_MCP_AUTH"]))

    async def test_admin_bekommt_beide(self):
        env = await self._env("a-admin")
        self.assertEqual(set(json.loads(env["CUSTOM_MCP_SERVERS"])), {"bestand", "neu"})

    async def test_rollenliste_gewinnt(self):
        await self._rollenliste([2])
        env = await self._env("a-m2")
        self.assertEqual(set(json.loads(env["CUSTOM_MCP_SERVERS"])), {"neu"})


class ListeTests(McpFuerAlleBasis):
    async def _liste(self, user):
        from app.api.mcp_servers import list_mcp_servers
        return (await list_mcp_servers(user=user, db=self.db))["servers"]

    async def test_mitglied_sieht_neuen_server_nicht(self):
        self.assertEqual([s["name"] for s in await self._liste(self.mitglied)], ["bestand"])

    async def test_mitglied_sieht_keine_url_und_keine_zugangsdaten(self):
        eintrag = (await self._liste(self.mitglied))[0]
        for feld in ("url", "has_auth", "has_headers", "oauth_enabled", "oauth_client_id",
                     "oauth_callback_base_url", "oauth_connected", "last_error", "fuer_alle"):
            self.assertNotIn(feld, eintrag)
        self.assertEqual(eintrag["tools"][0]["name"], "alt_tool")

    async def test_mit_fuer_alle_sieht_er_ihn(self):
        self.neu.fuer_alle = True
        await self.db.commit()
        self.assertEqual({s["name"] for s in await self._liste(self.mitglied)}, {"bestand", "neu"})

    async def test_rollenliste_gewinnt(self):
        await self._rollenliste([2])
        self.assertEqual([s["name"] for s in await self._liste(self.mit_rolle)], ["neu"])

    async def test_admin_sieht_alle_mit_url_und_schalter(self):
        liste = {s["name"]: s for s in await self._liste(self.admin)}
        self.assertEqual(liste["neu"]["url"], "https://neu.example.invalid/mcp")
        self.assertIs(liste["neu"]["fuer_alle"], False)
        self.assertIs(liste["bestand"]["fuer_alle"], True)


class ZuweisenTests(McpFuerAlleBasis):
    async def _zuweisen(self, user, agent_id, ids):
        from app.api import agents
        manager = SimpleNamespace(_get_agent=AsyncMock(side_effect=self._agent),
                                  restart_agent=AsyncMock())
        with patch.object(agents, "_check_owner", AsyncMock()):
            return await agents.update_agent_mcp_servers(agent_id, {"mcp_servers": ids},
                                                         user=user, db=self.db, manager=manager)

    async def _agent(self, agent_id):
        return await self.db.get(Agent, agent_id)

    async def test_nicht_nutzbarer_server_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as e:
            await self._zuweisen(self.mitglied, "a-m1", [1, 2])
        self.assertEqual(e.exception.status_code, 403)
        self.assertNotIn("mcp_servers", (await self._agent("a-m1")).config)

    async def test_nutzbarer_server_geht(self):
        antwort = await self._zuweisen(self.mitglied, "a-m1", [1])
        self.assertEqual(antwort["mcp_servers"], [1])

    async def test_admin_fuer_fremden_agenten_prueft_den_besitzer(self):
        """Was am Agenten haengt, bestimmen die Rechte des Besitzers."""
        with self.assertRaises(HTTPException) as e:
            await self._zuweisen(self.admin, "a-m1", [2])
        self.assertEqual(e.exception.status_code, 403)

    async def test_admin_eigener_agent_alles(self):
        antwort = await self._zuweisen(self.admin, "a-admin", [1, 2])
        self.assertEqual(antwort["mcp_servers"], [1, 2])

    async def test_null_heisst_alle_nutzbaren(self):
        antwort = await self._zuweisen(self.mitglied, "a-m1", None)
        self.assertIsNone(antwort["mcp_servers"])


class UmschaltenStartetNeuTests(McpFuerAlleBasis):
    async def _umschalten(self, wert):
        from app.api import mcp_servers as api
        manager = SimpleNamespace(restart_agent=AsyncMock())
        antwort = await api.update_mcp_server(2, api.McpServerUpdate(fuer_alle=wert),
                                              user=self.admin, db=self.db, manager=manager)
        return antwort, {c.args[0] for c in manager.restart_agent.await_args_list}

    async def test_anschalten_startet_nur_betroffene_agenten_neu(self):
        self.db.add_all([
            Agent(id="a-m1-aus", name="E", user_id="m1", state=AgentState.STOPPED, config={}),
            Agent(id="a-m1-ohne", name="F", user_id="m1", state=AgentState.RUNNING,
                  config={"mcp_servers": [1]}),
        ])
        await self.db.commit()
        antwort, neu_gestartet = await self._umschalten(True)
        self.assertIs(antwort["fuer_alle"], True)
        # Admin: unabhaengig; m2: Rollenliste entscheidet; gestoppt: startet frisch;
        # ausdruecklich abgewaehlt: aendert sich nichts.
        self.assertEqual(neu_gestartet, {"a-m1"})

    async def test_ausschalten_nimmt_ihn_den_mitgliedern_wieder(self):
        self.neu.fuer_alle = True
        await self.db.commit()
        _, neu_gestartet = await self._umschalten(False)
        self.assertEqual(neu_gestartet, {"a-m1"})
        self.assertEqual(await mcp_zugriff.nutzbare_mcp_server_ids(self.db, self.mitglied), {1})

    async def test_gleicher_wert_startet_nichts_neu(self):
        _, neu_gestartet = await self._umschalten(False)
        self.assertEqual(neu_gestartet, set())


if __name__ == "__main__":
    unittest.main()
