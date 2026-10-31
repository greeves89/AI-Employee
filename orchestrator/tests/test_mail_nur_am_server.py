"""E-Mail und Microsoft 365 nur am Server (P0-6 aus dem Bedrohungsmodell).

Bisher holte sich ein Werkzeug im Agenten das rohe Google- bzw. Microsoft-Token
(``GET /integrations/{provider}/for-agent``) und sprach Gmail und Graph direkt an:
Mit Bash und curl sendete ein Agent an jeder Regel vorbei, und der Microsoft-Weg
des alten E-Mail-Werkzeugs sendete auch ohne curl direkt — an Nur-Lesen, Entwurf
und Mail-Freigabe vorbei. Jetzt verlassen die Tokens den Orchestrator nicht.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import faehigkeiten, gmail_mcp
from app.models.agent import Agent
from app.models.user import User, UserRole


def _aufruf(name: str, **args) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}


async def _token():
    return "google-token"


class GmailTests(unittest.IsolatedAsyncioTestCase):
    """``core/gmail_mcp.py``: Senden richtet sich nach „E-Mail / M365 senden“."""

    def setUp(self):
        self.aufrufe = []

        async def _gmail(methode, pfad, token, **kw):
            self.aufrufe.append((methode, pfad, kw))
            if pfad.startswith("/messages/") and methode == "GET":
                return {"id": "m1", "threadId": "t1", "payload": {"headers": [
                    {"name": "From", "value": "kunde@example.invalid"},
                    {"name": "Subject", "value": "Frage"},
                    {"name": "Message-ID", "value": "<abc@example.invalid>"}]}}
            if pfad == "/drafts":
                return {"id": "d1"}
            return {}

        self.patch = patch.object(gmail_mcp, "_gmail", side_effect=_gmail)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    async def _rufe(self, body, senden):
        resp, status = await gmail_mcp.handle_mcp_request(body, _token, senden=senden)
        return resp, status

    def _text(self, resp) -> str:
        return resp["result"]["content"][0]["text"]

    async def test_erlaubt_wird_gesendet(self):
        resp, _ = await self._rufe(_aufruf("email_send", to="a@example.invalid", subject="Hallo", body="Text"), "allow")
        self.assertFalse(resp["result"]["isError"])
        self.assertEqual([p for _, p, _ in self.aufrufe], ["/messages/send"])

    async def test_freigabe_legt_nur_einen_entwurf_an(self):
        resp, _ = await self._rufe(_aufruf("email_send", to="a@example.invalid", subject="Hallo", body="Text"), "ask")
        self.assertIn("NICHT gesendet", self._text(resp))
        pfade = [p for _, p, _ in self.aufrufe]
        self.assertEqual(pfade, ["/drafts"])
        self.assertNotIn("/messages/send", pfade)

    async def test_antwort_mit_freigabe_ist_ebenfalls_entwurf(self):
        resp, _ = await self._rufe(_aufruf("email_reply", message_id="m1", body="Danke"), "ask")
        self.assertIn("NICHT gesendet", self._text(resp))
        self.assertEqual(self.aufrufe[-1][1], "/drafts")
        self.assertEqual(self.aufrufe[-1][2]["json"]["message"]["threadId"], "t1")

    async def test_verboten_sendet_nichts_und_zeigt_die_werkzeuge_nicht(self):
        for name, args in (("email_send", {"to": "a@example.invalid", "subject": "s", "body": "b"}),
                           ("email_reply", {"message_id": "m1", "body": "b"}),
                           ("email_mark_read", {"message_id": "m1"})):
            resp, _ = await self._rufe(_aufruf(name, **args), "deny")
            self.assertTrue(resp["result"]["isError"], name)
        self.assertEqual(self.aufrufe, [])
        resp, _ = await self._rufe({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "deny")
        namen = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(namen, {"email_list", "email_read", "email_search"})

    async def test_lesen_geht_auch_bei_verboten(self):
        resp, _ = await self._rufe(_aufruf("email_read", message_id="m1"), "deny")
        self.assertFalse(resp["result"]["isError"])

    async def test_keine_zusaetzlichen_kopfzeilen(self):
        """Ein Zeilenumbruch im Empfänger setzte sonst ein Bcc an beliebige Adressen."""
        for args in ({"to": "a@example.invalid\r\nBcc: b@example.invalid", "subject": "s", "body": "b"},
                     {"to": "a@example.invalid", "subject": "s\nBcc: b@example.invalid", "body": "b"}):
            resp, _ = await self._rufe(_aufruf("email_send", **args), "allow")
            self.assertTrue(resp["result"]["isError"])
        self.assertEqual(self.aufrufe, [])

    async def test_ungueltige_kennung(self):
        resp, _ = await self._rufe(_aufruf("email_read", message_id="../../settings"), "allow")
        self.assertTrue(resp["result"]["isError"])
        self.assertEqual(self.aufrufe, [])

    async def test_ohne_verbindung(self):
        async def _kein_token():
            return None

        resp, _ = await gmail_mcp.handle_mcp_request(_aufruf("email_list"), _kein_token, senden="allow")
        self.assertIn("not connected", self._text(resp))


class FaehigkeitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[User.__table__, Agent.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u1", email="u@example.invalid", name="U", role=UserRole.MEMBER),
                Agent(id="frei", name="L4", user_id="u1", config={}, autonomy_level="l4"),
                Agent(id="eigen", name="Matrix", user_id="u1", config={}, autonomy_level="l4",
                      access_policy={"autonomy_matrix": {"email_m365": "deny"}}),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_matrix_des_agenten_gilt(self):
        async with self.Session() as db:
            self.assertEqual(await faehigkeiten.zustand(db, "frei", "email_m365"), "allow")
            self.assertEqual(await faehigkeiten.zustand(db, "eigen", "email_m365"), "deny")

    async def test_unbekannt_heisst_verboten(self):
        async with self.Session() as db:
            self.assertEqual(await faehigkeiten.zustand(db, "gibtsnicht", "email_m365"), "deny")
            self.assertEqual(await faehigkeiten.zustand(db, "frei", "erfunden"), "deny")


class EndpunktTests(unittest.IsolatedAsyncioTestCase):

    async def test_falscher_agenten_token(self):
        from app.api.mcp_email import mcp_email_endpoint
        anfrage = MagicMock()
        anfrage.headers = {"Authorization": "Bearer falsch"}
        resp = await mcp_email_endpoint("a1", anfrage)
        self.assertEqual(resp.status_code, 401)

    def test_die_token_herausgabe_gibt_es_nicht_mehr(self):
        from app.api.integrations import router
        pfade = {r.path for r in router.routes}
        self.assertFalse(any(p.endswith("/for-agent") for p in pfade), pfade)

    def test_router_ist_eingebunden(self):
        from app.api.router import api_router
        self.assertIn("/mcp/email/{agent_id}", {r.path for r in api_router.routes})


class ZuordnungTests(unittest.IsolatedAsyncioTestCase):
    """Wer Google verbunden hat, bekommt das E-Mail-MCP als Adresse des Servers."""

    async def _env(self, integrationen):
        from app.core.agent_manager import AgentManager
        am = AgentManager(MagicMock(), MagicMock(), MagicMock())
        with patch("app.core.agent_mcp_servers.servers_for_agent", AsyncMock(return_value=[])):
            return await am._get_custom_mcp_env({}, "abcdef12-0000", integrationen)

    async def test_google_bekommt_die_server_adresse(self):
        import json
        env = await self._env(["google"])
        self.assertEqual(json.loads(env["CUSTOM_MCP_SERVERS"])["email"],
                         "http://ai-employee-orchestrator:8000/api/v1/mcp/email/abcdef12-0000")
        self.assertIn("email", json.loads(env["CUSTOM_MCP_AUTH"]))

    async def test_ohne_google_kein_email_mcp(self):
        import json
        env = await self._env(["microsoft"])
        servers = json.loads(env["CUSTOM_MCP_SERVERS"])
        self.assertNotIn("email", servers)
        self.assertIn("msgraph", servers)


if __name__ == "__main__":
    unittest.main()
