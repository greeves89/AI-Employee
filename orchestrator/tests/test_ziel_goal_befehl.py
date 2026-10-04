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


class OhneMarkeTests(unittest.TestCase):
    """#906: Die Schlusszeile ist ein Signal an den Server, kein Text für den Menschen."""

    def test_erreicht_wird_entfernt(self):
        text, zustand = ziel.ohne_marke("Drei Zeilen geschrieben.\n\n**ZIEL ERREICHT**")
        self.assertEqual(text, "Drei Zeilen geschrieben.")
        self.assertEqual(zustand, "erreicht")

    def test_pausiert_behaelt_die_frage(self):
        text, zustand = ziel.ohne_marke("Halb fertig.\nZIEL PAUSIERT: Welche Farbe?")
        self.assertEqual(text, "Halb fertig.\nWelche Farbe?")
        self.assertEqual(zustand, "pausiert")

    def test_gleiche_erkennung_wie_bewerte(self):
        """Was ``bewerte`` nicht als Meldung zählt, bleibt stehen."""
        mitten = "Ich melde ZIEL ERREICHT erst am Ende.\nA\nB\nC\nNoch drei Spiele offen."
        self.assertEqual(ziel.ohne_marke(mitten), (mitten, None))
        for antwort in ("Fertig.\nZIEL ERREICHT", "x\n> ZIEL PAUSIERT: Frage", "nichts"):
            _, zustand = ziel.ohne_marke(antwort)
            urteil = ziel.bewerte(antwort)
            self.assertEqual(zustand, None if urteil == "weiter" else urteil)

    def test_leer(self):
        self.assertEqual(ziel.ohne_marke(""), ("", None))


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


class BudgetSperrtRundeTests(unittest.IsolatedAsyncioTestCase):
    """Sperrt das Budget die nächste Runde, darf das Ziel nicht stumm auf „aktiv“
    stehen bleiben: Es pausiert mit dem Grund „Budget aufgebraucht“, der Hinweis
    steht im Gespräch, und nach einer Budgeterhöhung setzt die nächste Nachricht
    es fort — wie jedes pausierte Ziel.

    Gegen eine echte (SQLite-)Datenbank und die echte Budgetprüfung.
    """

    async def asyncSetUp(self):
        from datetime import datetime, timezone

        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        from app.core import budget
        from app.models.agent import Agent, AgentState
        from app.models.chat_message import ChatMessage
        from app.models.notification import Notification
        from app.models.task import Task
        from app.models.user import User, UserRole
        from tests.test_chat_einreihen_budget import FakeRedis

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001 — schon registriert
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent, Task, ChatMessage, Notification, ChatSession):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = FakeRedis()
        self.patches = [
            patch.multiple(budget.settings, platform_budget_usd=0.0, budget_gilt_fuer_chat=True,
                           model_provider="anthropic", display_currency="EUR", usd_eur_rate=0.5,
                           create=True),
            patch("app.db.session.async_session_factory", self.Session),
            patch("app.services.chat_persistence.async_session_factory", self.Session),
        ]
        for p in self.patches:
            p.start()
        async with self.Session() as db:
            db.add(User(id="u1", email="u1@example.invalid", name="U", role=UserRole.MEMBER))
            db.add(Agent(id="a1", name="Bauer", state=AgentState.IDLE, user_id="u1", config={},
                         budget_usd=1.0, budget_exceeded_action="stop"))
            # Budget aufgebraucht — durch Chat, nicht durch Aufgaben.
            db.add(ChatMessage(agent_id="a1", session_id="alt", message_id="x", role="assistant",
                               content="x", cost_usd=2.0, timestamp=datetime.now(timezone.utc)))
            await db.commit()
            await ziel.setzen(db, "a1", "s1", "Snake fertig")

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.engine.dispose()

    async def _zeile(self):
        async with self.Session() as db:
            return await db.scalar(select(ChatSession))

    async def test_gesperrte_runde_pausiert_mit_grund_und_hinweis(self):
        from app.models.chat_message import ChatMessage

        async with self.Session() as db:
            neu = await ziel.nach_zug(db, self.redis, "a1", "s1", "m1", "Snake steht, weiter.")
        self.assertEqual(neu, ziel.PAUSE)
        zeile = await self._zeile()
        self.assertEqual(zeile.goal_status, ziel.PAUSE)
        self.assertEqual(zeile.goal_reason, ziel.GRUND_BUDGET)
        self.assertEqual(ziel.als_dict(zeile)["reason"], "Budget aufgebraucht")
        # Nichts eingereiht.
        self.assertIsNone(self.redis.listen.get("agent:a1:chat"))
        # Der Hinweis steht dauerhaft im Gespräch (übersteht ein Neuladen) …
        async with self.Session() as db:
            fehler = (await db.execute(select(ChatMessage).where(
                ChatMessage.session_id == "s1", ChatMessage.role == "error"))).scalars().all()
        self.assertEqual(len(fehler), 1)
        self.assertIn("aufgebraucht", fehler[0].content)
        # … und ein offenes Fenster lädt den Ziel-Stand neu.
        arten = [e.get("type") for _, e in self.redis.veroeffentlicht]
        self.assertIn("goal", arten)

    async def test_nach_budgeterhoehung_setzt_die_naechste_nachricht_fort(self):
        from app.models.agent import Agent

        async with self.Session() as db:
            await ziel.nach_zug(db, self.redis, "a1", "s1", "m1", "Snake steht, weiter.")
        async with self.Session() as db:
            (await db.get(Agent, "a1")).budget_usd = 100.0
            await db.commit()
        # Der Mensch schreibt, der Agent antwortet — das Ziel läuft weiter.
        async with self.Session() as db:
            neu = await ziel.nach_zug(db, self.redis, "a1", "s1", "m2", "Tetris als Nächstes.")
        self.assertEqual(neu, ziel.AKTIV)
        zeile = await self._zeile()
        self.assertEqual(zeile.goal_status, ziel.AKTIV)
        self.assertIsNone(zeile.goal_reason)
        self.assertEqual(len(self.redis.listen["agent:a1:chat"]), 1)

    async def test_neues_ziel_vergisst_den_alten_grund(self):
        async with self.Session() as db:
            await ziel.nach_zug(db, self.redis, "a1", "s1", "m1", "weiter")
            await ziel.setzen(db, "a1", "s1", "Neues Ziel")
        zeile = await self._zeile()
        self.assertEqual((zeile.goal_status, zeile.goal_reason), (ziel.AKTIV, None))

    async def test_erste_runde_gesperrt(self):
        """``/goal`` im Chat (ws.py): Schon die erste Runde kann am Budget scheitern."""
        await ziel.budget_gesperrt(self.redis, "a1", "s1", "m0", "Das Budget ist aufgebraucht.")
        zeile = await self._zeile()
        self.assertEqual((zeile.goal_status, zeile.goal_reason), (ziel.PAUSE, ziel.GRUND_BUDGET))

    async def test_gestopptes_ziel_bleibt_gestoppt(self):
        """Eine Sperre pausiert nur ein laufendes Ziel — ein beendetes lebt nicht wieder auf."""
        async with self.Session() as db:
            await ziel.beenden(db, "a1", "s1")
        await ziel.budget_gesperrt(self.redis, "a1", "s1", "m0", "Das Budget ist aufgebraucht.")
        zeile = await self._zeile()
        self.assertEqual((zeile.goal_status, zeile.goal_reason), (ziel.GESTOPPT, None))
