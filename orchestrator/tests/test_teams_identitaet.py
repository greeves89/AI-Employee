"""Teams als eigene Identität des Agenten (Plan agenten-identitaet-m365, Phase 3).

Gegen eine echte Datenbank (SQLite) mit nachgebautem Graph:

* EIN Weg zum Senden (``teams_gateway.senden_nach_regeln``) für Kanal-Antwort und Werkzeug,
  nach denselben Regeln wie Mail (``ms_access.sendemodus``): Nur-Lesen-Schalter, Einstellung
  des Agenten, Sekretariat, Rechte-Matrix „E-Mail / M365 senden“, zugewiesene Anbindung.
  Bis 1.446 prüfte die Kanal-Antwort nur den Nur-Lesen-Schalter: Ein Agent auf „Lesen“
  schrieb trotzdem unter dem Namen seines Besitzers in Teams.
* Modus „Entwurf“: gebundene Freigabe ``teams_senden`` — der Server sendet nach dem Freigeben
  genau diesen Text, einmal; ein Agent kann so eine Freigabe nicht selbst anlegen.
* Schleifenschutz in zwei Halten: gesendete Nachrichten (auch über das Konto des Besitzers)
  und die eigene Kennung bei eigenem Konto — Nachrichten des Besitzers bleiben sichtbar.
* Wecken: Kanal-Nachrichten wecken gestoppte Agenten, nicht aber bewusst angehaltene;
  ebenso das Wecken bei neuer Mail.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "c2VjcmV0LWtleS1mb3ItdGVzdHMtb25seS0zMmJ5dGU=")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

from app.core import ms_access  # noqa: E402
from app.models.agent import Agent, AgentState  # noqa: E402
from app.models.agent_access import AgentAccess  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.command_approval import CommandApproval  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from app.models.team import Team  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.services import teams_gateway  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


class _Redis:
    def __init__(self):
        self.d, self.mengen = {}, {}

    async def sadd(self, k, v):
        self.mengen.setdefault(k, set()).add(v)

    async def sismember(self, k, v):
        return v in self.mengen.get(k, set())

    async def expire(self, k, t):
        return True

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None, nx=False):
        self.d[k] = v
        return True

    async def delete(self, k):
        self.d.pop(k, None)


def _matrix(email):
    return {"autonomy_matrix": {"email_m365": email}}


class _Basis(unittest.IsolatedAsyncioTestCase):
    ZIEL = {"chat_id": "19:abc@thread.v2"}

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent, AgentAccess, AuditLog, CommandApproval, Notification, Team):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u1", email="besitzer@example.com", name="Besitzer", role=UserRole.MEMBER),
                Agent(id="a1", name="Assistenz", state=AgentState.RUNNING, user_id="u1",
                      config={"integrations": ["microsoft"], "msgraph_access": "write"},
                      access_policy=_matrix("allow")),
            ])
            await db.commit()
        self.redis = _Redis()
        self.gesendet = []

        async def graph(method, path, token, **kw):
            if method == "POST":
                self.gesendet.append((path, kw.get("content")))
                return {"id": f"msg-{len(self.gesendet)}"}
            if path.startswith("/me"):
                return {"id": "agent-oid"}
            raise AssertionError(path)

        self._p = [
            patch("app.config.settings.msgraph_read_only", False),
            patch.object(teams_gateway, "resilient_session", self.Session),
            patch.object(teams_gateway, "_agenten_token", AsyncMock(return_value="tok")),
            patch.object(teams_gateway, "_redis", AsyncMock(return_value=SimpleNamespace(client=self.redis))),
            patch("app.core.msgraph_mcp._graph", graph),
            patch("app.api.approvals._get_redis", return_value=None),
            patch("app.api.approvals._push_ios_for_agent", AsyncMock()),
        ]
        for p in self._p:
            p.start()

    async def asyncTearDown(self):
        for p in self._p:
            p.stop()
        await self.engine.dispose()

    async def _setzen(self, **kw):
        async with self.Session() as db:
            a = await db.get(Agent, "a1")
            if "access" in kw:
                a.config = {**(a.config or {}), "msgraph_access": kw["access"]}
            if "integrations" in kw:
                a.config = {**(a.config or {}), "integrations": kw["integrations"]}
            if "email" in kw:
                a.access_policy = _matrix(kw["email"])
            await db.commit()

    async def _agent(self):
        async with self.Session() as db:
            return await db.get(Agent, "a1")

    async def _senden(self, text="Hallo **Team**", **kw):
        return await teams_gateway.senden_nach_regeln(await self._agent(), self.ZIEL, text, **kw)

    async def _freigaben(self):
        async with self.Session() as db:
            return (await db.execute(select(CommandApproval))).scalars().all()


class SendemodusTests(_Basis):
    """Die eine Entscheidung — dieselbe für Werkzeug und Kanal (MCDC über die Eingänge)."""

    async def _modus(self):
        async with self.Session() as db:
            return await ms_access.sendemodus(db, await db.get(Agent, "a1"))

    async def test_matrix_der_eingaenge(self):
        faelle = [
            # (Nur-Lesen, Einstellung, Matrix) → Modus
            (False, "write", "allow", ms_access.SCHREIBEN),
            (True, "write", "allow", ms_access.LESEN),
            (False, "read", "allow", ms_access.LESEN),
            (False, "write", "ask", ms_access.ENTWURF),
            (False, "write", "deny", ms_access.LESEN),
            (False, "entwurf", "allow", ms_access.ENTWURF),
            (False, "entwurf", "deny", ms_access.LESEN),
            (False, "read", "ask", ms_access.LESEN),
        ]
        for nur_lesen, access, email, erwartet in faelle:
            await self._setzen(access=access, email=email)
            with patch("app.config.settings.msgraph_read_only", nur_lesen):
                self.assertEqual(await self._modus(), erwartet, (nur_lesen, access, email))

    async def test_sekretariat_stuft_schreiben_auf_entwurf(self):
        async with self.Session() as db:
            db.add(Team(id="t1", name="Sekretariat", art="sekretariat", is_active=True, lead_agent_id="a1",
                        member_agent_ids=[]))
            await db.commit()
        self.assertEqual(await self._modus(), ms_access.ENTWURF)


class SendenTests(_Basis):
    async def test_schreiben_sendet_und_merkt_als_eigen(self):
        self.assertEqual(await self._senden(), "gesendet")
        self.assertEqual(self.gesendet[0][0], "/chats/19%3Aabc%40thread.v2/messages")
        self.assertIn("<b>Team</b>", self.gesendet[0][1])
        self.assertTrue(await teams_gateway._ist_eigen("a1", "msg-1"))

    async def test_nur_lesen_lesen_oder_ohne_anbindung_sendet_nichts(self):
        with patch("app.config.settings.msgraph_read_only", True):
            self.assertEqual(await self._senden(), "aus")
        await self._setzen(access="read")
        self.assertEqual(await self._senden(), "aus")
        await self._setzen(access="write", integrations=[])
        self.assertEqual(await self._senden(), "aus")
        self.assertEqual(self.gesendet, [])
        self.assertEqual(await self._freigaben(), [])

    async def test_kanal_antwort_folgt_denselben_regeln(self):
        """Der Fund: ``send_reply`` prüfte nur den Nur-Lesen-Schalter."""
        await self._setzen(access="read")
        self.assertFalse(await teams_gateway.send_reply(await self._agent(), {"chat_id": "19:x"}, "Hallo"))
        self.assertEqual(self.gesendet, [])

    async def test_entwurf_legt_gebundene_freigabe_an_und_sendet_erst_danach(self):
        from app.api import approvals

        await self._setzen(email="ask")
        self.assertEqual(await self._senden("Angebot kommt morgen"), "freigabe")
        self.assertEqual(self.gesendet, [])
        f = (await self._freigaben())[0]
        self.assertEqual(f.meta["kind"], "teams_senden")
        self.assertEqual(f.meta["view"]["name"], "handlung")
        self.assertIn("Angebot kommt morgen", str(f.meta["view"]["data"]))
        async with self.Session() as db:
            with patch.object(approvals, "_darf_entscheiden", AsyncMock()):
                await approvals.approve_request(str(f.id), None, user=await db.get(User, "u1"), db=db)
        self.assertEqual(len(self.gesendet), 1)
        self.assertIn("Angebot kommt morgen", self.gesendet[0][1])

    async def test_freigabe_nach_umstellen_auf_nur_lesen_sendet_nicht(self):
        from app.api import approvals

        await self._setzen(email="ask")
        await self._senden()
        f = (await self._freigaben())[0]
        async with self.Session() as db:
            with patch("app.config.settings.msgraph_read_only", True), \
                    patch.object(approvals, "_darf_entscheiden", AsyncMock()), \
                    self.assertRaises(HTTPException) as e:
                await approvals.approve_request(str(f.id), None, user=await db.get(User, "u1"), db=db)
        self.assertEqual(e.exception.status_code, 409)
        self.assertEqual(self.gesendet, [])

    def test_agent_kann_teams_freigabe_nicht_selbst_anlegen(self):
        from app.api import approvals

        self.assertTrue(approvals.ist_server_art("teams_senden"))
        # Auch wenn die Anmeldung der Ausführung (aus welchem Grund auch immer) fehlt:
        with patch.dict("app.core.gebundene_freigabe.AUSFUEHRER", {}, clear=True), \
                patch("app.core.gebundene_freigabe.anmeldungen_laden"):
            self.assertTrue(approvals.ist_server_art("teams_senden"))

    def test_ausfuehrung_wird_vor_dem_ausfuehren_geladen(self):
        """Ohne angemeldete Ausführung schlösse eine Freigabe still, ohne zu senden — deshalb
        lädt ``ausfuehren``/``ist_server_art`` die anmeldenden Module selbst."""
        import importlib

        from app.core import gebundene_freigabe

        with patch.object(importlib, "import_module", wraps=importlib.import_module) as laden:
            gebundene_freigabe.anmeldungen_laden()
        self.assertIn("app.services.teams_gateway", [c.args[0] for c in laden.call_args_list])
        self.assertIs(gebundene_freigabe.AUSFUEHRER["teams_senden"], teams_gateway._freigegeben_senden)


class WerkzeugTests(_Basis):
    """Werkzeuge im Modus „Entwurf“: Teams-Senden sichtbar, aber nur über die Freigabe."""

    async def _mcp(self, name, args, teams_freigabe):
        from app.core.msgraph_mcp import handle_mcp_request

        return await handle_mcp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
            AsyncMock(return_value="tok"), write_enabled=False, entwurf_freigabe=AsyncMock(),
            teams_freigabe=teams_freigabe)

    async def test_werkzeug_geht_zur_freigabe_nicht_an_graph(self):
        freigabe = AsyncMock(return_value="wartet")
        (antwort, _) = await self._mcp("ms_send_chat_message", {"chat_id": "19:x", "message": "Hi"}, freigabe)
        freigabe.assert_awaited_once_with({"chat_id": "19:x"}, "Hi")
        self.assertIn("wartet", str(antwort))
        self.assertEqual(self.gesendet, [])
        (antwort, _) = await self._mcp("ms_send_teams_message",
                                       {"team_id": "t", "channel_id": "c", "message": "Hi"}, freigabe)
        self.assertEqual(freigabe.await_args.args[0], {"team_id": "t", "channel_id": "c"})

    async def test_ohne_freigabe_weg_nicht_gesendet(self):
        from app.core.msgraph_mcp import handle_mcp_request

        (antwort, _) = await handle_mcp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "ms_send_chat_message", "arguments": {"chat_id": "19:x", "message": "Hi"}}},
            AsyncMock(return_value="tok"), write_enabled=False, entwurf_freigabe=AsyncMock(), teams_freigabe=None)
        self.assertTrue(antwort["result"]["isError"])
        self.assertEqual(self.gesendet, [])

    async def test_nur_lesen_sieht_keine_teams_werkzeuge(self):
        from app.core.msgraph_mcp import handle_mcp_request

        (antwort, _) = await handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                                AsyncMock(), write_enabled=False)
        namen = {t["name"] for t in antwort["result"]["tools"]}
        self.assertNotIn("ms_send_chat_message", namen)


class SchleifenschutzTests(_Basis):
    def _nachricht(self, mid, absender, text="Assistenz bitte"):
        return {"id": mid, "createdDateTime": "2099-01-01T00:00:00Z", "messageType": "message",
                "from": {"user": {"id": absender, "displayName": "x"}},
                "body": {"contentType": "text", "content": text}}

    async def _abfragen(self, nachrichten, eigenes_konto: bool):
        from datetime import datetime, timezone

        zugestellt = []
        gw = teams_gateway.TeamsGateway(SimpleNamespace(client=self.redis))

        async def liste(method, path, token, **kw):
            if path.startswith("/me"):
                return {"id": "agent-oid"}
            return {"value": nachrichten}

        with patch("app.core.msgraph_mcp._graph", liste), \
                patch("app.core.agenten_konto._zeile", AsyncMock(return_value=object() if eigenes_konto else None)), \
                patch.object(teams_gateway.gw, "deliver", AsyncMock(side_effect=lambda r, m: zugestellt.append(m.message_id) or True)):
            await gw._poll_source(await self._agent(), {"mention_only": False}, "tok",
                                  datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2000, 1, 1, tzinfo=timezone.utc),
                                  path="/chats/x/messages", context={"chat_id": "x"})
        return zugestellt

    async def test_eigene_gesendete_nachricht_kommt_nicht_zurueck(self):
        await teams_gateway._als_eigen_merken("a1", "m-eigen")
        erg = await self._abfragen([self._nachricht("m-eigen", "besitzer-oid"),
                                    self._nachricht("m-mensch", "besitzer-oid")], eigenes_konto=False)
        self.assertEqual(erg, ["m-mensch"], "Der Besitzer bleibt ansprechbar, nur die eigene Antwort fällt weg")

    async def test_mit_eigenem_konto_alles_von_der_eigenen_kennung_weg(self):
        erg = await self._abfragen([self._nachricht("m1", "agent-oid"), self._nachricht("m2", "kollege-oid")],
                                   eigenes_konto=True)
        self.assertEqual(erg, ["m2"])

    async def test_ohne_eigenes_konto_wird_besitzer_nicht_zur_eigenen_kennung(self):
        erg = await self._abfragen([self._nachricht("m1", "agent-oid")], eigenes_konto=False)
        self.assertEqual(erg, ["m1"], "/me ist dann der Besitzer — der darf den Agenten ansprechen")


class WeckenTests(unittest.IsolatedAsyncioTestCase):
    async def _zustellen(self, kanal, angehalten=False):
        from app.core import channel_gateway as gw

        agent = SimpleNamespace(id="a1", config={"angehalten_vom_nutzer": True} if angehalten else {})
        sitzung = MagicMock()
        sitzung.__aenter__ = AsyncMock(return_value=SimpleNamespace(get=AsyncMock(return_value=agent)))
        sitzung.__aexit__ = AsyncMock(return_value=False)
        nachricht = gw.InboundMessage(agent_id="a1", text="Hallo", channel=kanal, conversation_id="c",
                                      message_id="m", context={})
        with patch.object(gw, "already_seen", AsyncMock(return_value=False)), \
                patch.object(gw, "persist_message", AsyncMock()), \
                patch.object(gw, "capture_if_worthwhile", AsyncMock()), \
                patch.object(gw, "enqueue", AsyncMock(return_value=SimpleNamespace(eingereiht=True))), \
                patch("app.services.user_lifecycle.mark_agent_interaction", AsyncMock()), \
                patch("app.db.session.async_session_factory", return_value=sitzung), \
                patch("app.core.agent_duty.vom_nutzer_angehalten", return_value=angehalten), \
                patch("app.services.docker_service.DockerService", MagicMock()), \
                patch("app.services.user_lifecycle.wake_agent", AsyncMock()) as wecken:
            self.assertTrue(await gw.deliver(None, nachricht))
        return wecken

    async def test_kanal_nachricht_weckt_gestoppten_agenten(self):
        from app.core import channel_gateway as gw

        for kanal in (gw.CHANNEL_TEAMS, gw.CHANNEL_SLACK, gw.CHANNEL_DISCORD):
            (await self._zustellen(kanal)).assert_awaited_once()

    async def test_bewusst_angehalten_oder_telegram_kein_wecken(self):
        from app.core import channel_gateway as gw

        (await self._zustellen(gw.CHANNEL_TEAMS, angehalten=True)).assert_not_awaited()
        (await self._zustellen(gw.CHANNEL_TELEGRAM)).assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
