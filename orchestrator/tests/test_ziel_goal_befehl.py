"""/goal — der Agent arbeitet Runde um Runde, bis er das Ziel als erreicht meldet.

MC/DC ueber die Entscheidung nach einer fertigen Antwort (app.core.ziel.nach_zug):
Ziel aktiv/pausiert/gestoppt × Urteil (erreicht/pausiert/weiter/Fehler) × Grenze ×
dieselbe Antwort zum zweiten Mal (Live-Kanal + dauerhafte Liste).
"""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import ziel
from app.models.chat_session import ChatSession


class BefehlTests(unittest.TestCase):
    def test_setzen(self):
        self.assertEqual(ziel.befehl("/goal 20 Spiele fertig"), ("setzen", "20 Spiele fertig"))
        self.assertEqual(ziel.befehl("/ziel Mehrzeilig\nzweite Zeile"), ("setzen", "Mehrzeilig\nzweite Zeile"))

    def test_stopp_und_status(self):
        self.assertEqual(ziel.befehl("/goal stop"), ("stopp", ""))
        self.assertEqual(ziel.befehl("/goal"), ("status", ""))

    def test_kein_befehl(self):
        self.assertIsNone(ziel.befehl("Bitte setz ein /goal"))
        self.assertIsNone(ziel.befehl("/goals sind toll"))

    def test_urteil_nur_aus_den_schlusszeilen(self):
        self.assertEqual(ziel.bewerte("Alles gebaut und getestet.\n\n**ZIEL ERREICHT**"), "erreicht")
        self.assertEqual(ziel.bewerte("ZIEL PAUSIERT: Welche Farbe?"), "pausiert")
        mitten = "Ich melde ZIEL ERREICHT erst am Ende.\nA\nB\nC\nNoch drei Spiele offen."
        self.assertEqual(ziel.bewerte(mitten), "weiter")

    def test_auftrag_nennt_ziel_und_regeln(self):
        t = ziel.auftrag("Snake bauen", 3)
        self.assertIn("Snake bauen", t)
        self.assertIn("Runde 3", t)
        self.assertIn(ziel.ERREICHT, t)


class NachZugTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(ChatSession.metadata.create_all, tables=[ChatSession.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = AsyncMock()
        self.runde = patch.object(ziel, "naechste_runde", AsyncMock(return_value="neu"))
        self.naechste = self.runde.start()

    async def asyncTearDown(self):
        self.runde.stop()
        await self.engine.dispose()

    async def _setzen(self, status=None, runden=0):
        async with self.Session() as db:
            await ziel.setzen(db, "a1", "s1", "Snake fertig")
            if status or runden:
                z = await db.scalar(select(ChatSession))
                z.goal_status = status or z.goal_status
                z.goal_rounds = runden
                await db.commit()

    async def _zug(self, mid, antwort, fehler=False):
        async with self.Session() as db:
            return await ziel.nach_zug(db, self.redis, "a1", "s1", mid, antwort, fehlgeschlagen=fehler)

    async def _stand(self):
        async with self.Session() as db:
            z = await db.scalar(select(ChatSession))
            return z.goal_status, z.goal_rounds

    async def test_nicht_erreicht_naechste_runde(self):
        await self._setzen()
        self.assertEqual(await self._zug("m1", "Snake steht, Tetris als Naechstes."), ziel.AKTIV)
        self.assertEqual(await self._stand(), (ziel.AKTIV, 1))
        self.naechste.assert_awaited_once()
        self.assertEqual(self.naechste.await_args.args[4], 2)

    async def test_erreicht_beendet(self):
        await self._setzen()
        self.assertEqual(await self._zug("m1", "Fertig.\nZIEL ERREICHT"), ziel.ERLEDIGT)
        self.naechste.assert_not_awaited()

    async def test_pausiert_wartet_und_naechste_nutzerantwort_setzt_fort(self):
        await self._setzen()
        self.assertEqual(await self._zug("m1", "ZIEL PAUSIERT: Farbe?"), ziel.PAUSE)
        self.naechste.assert_not_awaited()
        self.assertEqual(await self._zug("m2", "Blau umgesetzt, weiter mit Tetris."), ziel.AKTIV)
        self.naechste.assert_awaited_once()

    async def test_fehler_pausiert_statt_im_kreis(self):
        await self._setzen()
        self.assertEqual(await self._zug("m1", "", fehler=True), ziel.PAUSE)
        self.naechste.assert_not_awaited()

    async def test_obergrenze(self):
        await self._setzen(runden=ziel.ZIEL_MAX_RUNDEN - 1)
        self.assertEqual(await self._zug("m1", "noch nicht"), ziel.GRENZE)
        self.naechste.assert_not_awaited()

    async def test_dieselbe_antwort_zaehlt_nur_einmal(self):
        await self._setzen()
        await self._zug("m1", "weiter")
        self.assertIsNone(await self._zug("m1", "weiter"))
        self.assertEqual(self.naechste.await_count, 1)
        self.assertEqual((await self._stand())[1], 1)

    async def test_gestoppt_macht_nichts(self):
        await self._setzen()
        async with self.Session() as db:
            self.assertTrue(await ziel.beenden(db, "a1", "s1"))
        self.assertIsNone(await self._zug("m1", "weiter"))
        self.naechste.assert_not_awaited()

    async def test_ohne_ziel_macht_nichts(self):
        self.assertIsNone(await self._zug("m1", "irgendwas"))


if __name__ == "__main__":
    unittest.main()


class NaechsteRundeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runde_landet_in_der_warteschlange_des_agenten(self):
        import json
        redis = AsyncMock()
        with patch("app.services.chat_persistence.upsert_chat_message", AsyncMock(return_value=True)) as zeile:
            mid = await ziel.naechste_runde(redis, "a1", "s1", "Snake fertig", 3)
        schluessel, nutzlast = redis.lpush.await_args.args
        self.assertEqual(schluessel, "agent:a1:chat")
        daten = json.loads(nutzlast)
        self.assertEqual((daten["id"], daten["chat_session_id"]), (mid, "s1"))
        self.assertIn("Runde 3", daten["text"])
        # Im Verlauf eine schlanke Statuszeile, an der die Antwort ihr Gespraech findet.
        args, kwargs = zeile.await_args
        self.assertEqual(args[:4], ("a1", "s1", mid, "user"))
        self.assertEqual(kwargs["meta"]["source"], "goal")
