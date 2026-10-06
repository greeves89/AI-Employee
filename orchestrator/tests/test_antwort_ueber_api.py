"""Agenten antworten über die API, nicht direkt über Redis (#918).

Der Rückweg muss dafür ausdrücklich offen sein: Erreichbarkeit ist nicht
symmetrisch (einen Plattform-Agenten darf jeder ansprechen, der kennt den
Fragenden aber nicht als Kollegen). Offen ist genau die Antwort auf eine echte,
gespeicherte Frage dieses Empfängers — nicht mehr.
"""

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.agents import _ist_antwort_an_fragenden
from app.models.agent_message import AgentMessage


class RueckwegNurFuerEchteFragen(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AgentMessage.metadata.create_all, tables=[AgentMessage.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            # "frager" hat "plattform" etwas gefragt.
            db.add(AgentMessage(message_id="m1", from_agent_id="frager", from_agent_name="F",
                                to_agent_id="plattform", text="Frage", message_type="question"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _darf(self, **kw):
        async with self.Session() as db:
            return await _ist_antwort_an_fragenden(db, **kw)

    async def test_antwort_auf_die_eigene_frage_des_empfaengers(self):
        self.assertTrue(await self._darf(absender="plattform", empfaenger="frager", reply_to="m1"))

    async def test_ohne_bezug_keine_ausnahme(self):
        self.assertFalse(await self._darf(absender="plattform", empfaenger="frager", reply_to=None))

    async def test_nur_eine_antwort_keine_rueckfrage_oder_uebergabe(self):
        for typ in ("question", "handoff", "message", None):
            self.assertFalse(await self._darf(absender="plattform", empfaenger="frager",
                                              reply_to="m1", message_type=typ), typ)

    async def test_genau_einmal_je_frage(self):
        async with self.Session() as db:
            db.add(AgentMessage(message_id="a1", from_agent_id="plattform", from_agent_name="P",
                                to_agent_id="frager", text="Antwort", message_type="response",
                                reply_to="m1"))
            await db.commit()
        self.assertFalse(await self._darf(absender="plattform", empfaenger="frager", reply_to="m1"))

    async def test_auf_eine_antwort_kann_sich_niemand_berufen(self):
        """Antworten älterer Agenten gelangen über einen Sammelkanal in die Tabelle
        — eine dort hinterlegte Zeile darf keinen Rückweg öffnen."""
        async with self.Session() as db:
            db.add(AgentMessage(message_id="gefaelscht", from_agent_id="opfer", from_agent_name="O",
                                to_agent_id="angreifer", text="x", message_type="response"))
            await db.commit()
        self.assertFalse(await self._darf(absender="angreifer", empfaenger="opfer", reply_to="gefaelscht"))

    async def test_fremde_frage_oeffnet_nichts(self):
        """Die Kennung einer fremden Nachricht reicht nicht — sie muss vom
        Empfänger an genau diesen Absender gegangen sein."""
        self.assertFalse(await self._darf(absender="angreifer", empfaenger="frager", reply_to="m1"))
        self.assertFalse(await self._darf(absender="plattform", empfaenger="dritter", reply_to="m1"))
        self.assertFalse(await self._darf(absender="plattform", empfaenger="frager", reply_to="gibt-es-nicht"))


if __name__ == "__main__":
    unittest.main()


class EndpunktHaeltDenRueckwegEng(unittest.IsolatedAsyncioTestCase):
    """Der Endpunkt selbst: Absender eines Menschen und die Einmal-Grenze."""

    async def _senden(self, body, *, user, erreichbar=(), darf_antworten=True, einmal=True):
        from unittest.mock import AsyncMock, MagicMock, patch

        from fastapi import HTTPException

        from app.api import agents, tasks

        class _Ende(Exception):
            pass

        manager = MagicMock(_get_agent=AsyncMock(side_effect=_Ende()))
        redis = MagicMock()
        redis.client.hgetall = AsyncMock(return_value={})
        redis.client.set = AsyncMock(return_value=einmal)
        self.redis = redis
        with patch.object(tasks, "_erreichbare_agenten", AsyncMock(return_value=set(erreichbar))), \
                patch.object(agents, "_check_owner", AsyncMock()), \
                patch.object(agents, "_ist_antwort_an_fragenden", AsyncMock(return_value=darf_antworten)):
            try:
                await agents.send_message_to_agent(
                    "ziel", body, user=user, db=MagicMock(get=AsyncMock(return_value=None)),
                    manager=manager, redis=redis)
            except _Ende:
                return 200
            except HTTPException as e:
                return e.status_code

    async def test_mensch_kann_keinen_agenten_als_absender_vortaeuschen(self):
        from types import SimpleNamespace

        from app.api.agents import AgentMessage
        from app.models.user import UserRole

        body = AgentMessage(text="Hallo", from_agent_id="fremder-agent")
        mensch = SimpleNamespace(id="u1", role=UserRole.MEMBER, email="u1@example.invalid", name="U")
        await self._senden(body, user=mensch)
        self.assertIsNone(body.from_agent_id)

    async def test_antwort_ausserhalb_der_kollegen_nur_einmal_auch_gleichzeitig(self):
        from app.api.agents import AgentMessage
        from app.dependencies import AgentPrincipal

        agent = AgentPrincipal(id="plattform", username="agent-plattform")
        erste = await self._senden(AgentMessage(text="A", message_type="response", reply_to="m1"),
                                   user=agent, einmal=True)
        self.assertEqual(erste, 200)
        schluessel = self.redis.client.set.call_args.args[0]
        self.assertEqual(schluessel, "antwort:einmal:plattform:ziel:m1")
        self.assertTrue(self.redis.client.set.call_args.kwargs.get("nx"), "unteilbar gesetzt")
        zweite = await self._senden(AgentMessage(text="A", message_type="response", reply_to="m1"),
                                    user=agent, einmal=None)
        self.assertEqual(zweite, 403)

    async def test_kollegen_brauchen_die_ausnahme_nicht(self):
        from app.api.agents import AgentMessage
        from app.dependencies import AgentPrincipal

        agent = AgentPrincipal(id="a1", username="agent-a1")
        code = await self._senden(AgentMessage(text="A", message_type="response", reply_to="m1"),
                                  user=agent, erreichbar=("ziel",), darf_antworten=False)
        self.assertEqual(code, 200)
        self.redis.client.set.assert_not_called()
