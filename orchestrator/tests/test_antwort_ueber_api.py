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
