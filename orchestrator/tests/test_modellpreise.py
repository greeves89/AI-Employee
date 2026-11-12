"""Preis je Modell aus dem KI-Konto und die Nachrechnung von Kosten (core/modellpreise).

Anlass (SKBS, 12.11.2026): Ein Kodierlauf mit gpt-6.1-sol über Azure meldete 1,7 Mio.
Eingabe-Token und 0 USD — der Agent kennt den Preis der gpt-6-Familie nicht. Dashboard
und Monatsbudget sahen dadurch nichts. Jetzt trägt die Administration den Preis je Modell
im KI-Konto ein, und der Orchestrator rechnet nach, wenn ein Lauf keinen Betrag meldet.
"""

import asyncio
import contextlib
import unittest
from unittest.mock import AsyncMock, patch

from app.api.ai_accounts import AIModelEntry, _normalize_models
from app.core import modellpreise as mp


class PreisPruefen(unittest.TestCase):
    def test_gueltige_und_ungueltige_werte(self):
        self.assertEqual(mp.preis_pruefen("2.5"), 2.5)
        self.assertEqual(mp.preis_pruefen(0), 0.0)
        for schlecht in (None, "", "abc", -1, 1001, float("nan"), True):
            self.assertIsNone(mp.preis_pruefen(schlecht), schlecht)


class PreisAusKonto(unittest.TestCase):
    MODELLE = (
        {"name": "gpt-6.1-sol", "preis_eingabe_usd_1m": 2.4, "preis_ausgabe_usd_1m": 12},
        {"name": "gpt-6-luna"},
        {"name": "halb", "preis_eingabe_usd_1m": 1},
    )

    def test_treffer_ohne_gross_klein(self):
        self.assertEqual(mp.preis_aus_konto(self.MODELLE, "GPT-6.1-sol"), (2.4, 12.0))

    def test_ohne_oder_mit_halbem_preis_none(self):
        self.assertIsNone(mp.preis_aus_konto(self.MODELLE, "gpt-6-luna"))
        self.assertIsNone(mp.preis_aus_konto(self.MODELLE, "halb"))
        self.assertIsNone(mp.preis_aus_konto(self.MODELLE, "fehlt"))
        self.assertIsNone(mp.preis_aus_konto(None, "gpt-6.1-sol"))


class Betrag(unittest.TestCase):
    PREIS = (2.0, 10.0)

    def test_gemeldeter_betrag_gewinnt(self):
        self.assertEqual(mp.betrag(0.5, 1_000_000, 0, self.PREIS), 0.5)

    def test_nachrechnen_bei_null_oder_fehlend(self):
        self.assertEqual(mp.betrag(0, 1_707_935, 11_050, self.PREIS), round(1_707_935 / 1e6 * 2 + 11_050 / 1e6 * 10, 6))
        self.assertEqual(mp.betrag(None, 1_000_000, 1_000_000, self.PREIS), 12.0)

    def test_ohne_preis_oder_token_nichts_erfinden(self):
        self.assertEqual(mp.betrag(0, 1_000_000, 0, None), 0)
        self.assertIsNone(mp.betrag(None, 0, 0, self.PREIS))
        self.assertEqual(mp.betrag(0, "kaputt", None, self.PREIS), 0)


class KatalogRueckfall(unittest.TestCase):
    def test_bekanntes_und_unbekanntes_modell(self):
        self.assertEqual(mp.preis_aus_katalog("gpt-5"), (1.25, 10.00))
        self.assertIsNone(mp.preis_aus_katalog("gpt-6.1-sol"))
        self.assertIsNone(mp.preis_aus_katalog(None))


class KontoSchema(unittest.TestCase):
    def test_preise_werden_gespeichert_und_ausgeliefert(self):
        e = AIModelEntry(name="gpt-6.1-sol", provider_type="azure-openai",
                         preis_eingabe_usd_1m=2.4, preis_ausgabe_usd_1m=12)
        roh = [e.model_dump()]
        aus = _normalize_models(roh, "azure-openai", None)[0]
        self.assertEqual((aus["preis_eingabe_usd_1m"], aus["preis_ausgabe_usd_1m"]), (2.4, 12.0))

    def test_ohne_preis_kein_feld(self):
        aus = _normalize_models([{"name": "m"}], "azure-openai", None)[0]
        self.assertNotIn("preis_eingabe_usd_1m", aus)

    def test_ungueltiger_preis_wird_abgewiesen(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            AIModelEntry(name="m", provider_type="azure-openai", preis_eingabe_usd_1m=-1)


class PreisFuerAgent(unittest.TestCase):
    def setUp(self):
        mp.cache_leeren()

    def test_konto_vor_katalog_und_cache(self):
        class Agent:
            model = "gpt-6.1-sol"
            ai_account_id = 1

        class Konto:
            models = ({"name": "gpt-6.1-sol", "preis_eingabe_usd_1m": 2.4, "preis_ausgabe_usd_1m": 12},)

        db = AsyncMock()
        db.get = AsyncMock(side_effect=lambda klasse, _id: Agent() if klasse.__name__ == "Agent" else Konto())
        preis = asyncio.run(mp.preis_fuer_agent("a1", db=db))
        self.assertEqual(preis, (2.4, 12.0))
        aufrufe = db.get.await_count
        asyncio.run(mp.preis_fuer_agent("a1", db=db))
        self.assertEqual(db.get.await_count, aufrufe, "zweiter Aufruf aus dem Zwischenspeicher")

    def test_ohne_kontopreis_kein_rueckfall_auf_tabelle(self):
        """Abo-Läufe (Claude/Codex) melden zu Recht 0 USD — keine Tabellenpreise nachrechnen."""
        class Agent:
            model = "claude-sonnet-4-6"
            ai_account_id = None

        db = AsyncMock()
        db.get = AsyncMock(return_value=Agent())
        self.assertIsNone(asyncio.run(mp.preis_fuer_agent("a3", db=db)))

    def test_fehler_stoert_nicht(self):
        db = AsyncMock()
        db.get = AsyncMock(side_effect=RuntimeError("weg"))
        self.assertIsNone(asyncio.run(mp.preis_fuer_agent("a2", db=db)))


if __name__ == "__main__":
    unittest.main()


class ChatZeileMitKontopreis(unittest.IsolatedAsyncioTestCase):
    """Ende-zu-Ende gegen SQLite: Chat-Antwort mit 0 USD und Token → Betrag aus dem KI-Konto."""

    async def asyncSetUp(self):
        from app.models.agent import Agent, AgentState
        from app.models.ai_account import AIAccount
        from app.models.chat_message import ChatMessage
        from app.models.user import User, UserRole
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.ext.compiler import compiles

        with contextlib.suppress(Exception):  # schon registriert
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        mp.cache_leeren()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, AIAccount, Agent, ChatMessage):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u", email="u@example.invalid", name="U", password_hash="x", role=UserRole.MEMBER),
                AIAccount(id=1, name="Konto", provider_type="azure-openai", models=[
                    {"name": "gpt-6.1-sol", "provider_type": "azure-openai", "api_endpoint": "",
                     "preis_eingabe_usd_1m": 2.0, "preis_ausgabe_usd_1m": 10.0}], extra={}),
                Agent(id="k1", name="Kodier", state=AgentState.IDLE, user_id="u", config={},
                      model="gpt-6.1-sol", ai_account_id=1),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        mp.cache_leeren()

    async def test_betrag_landet_in_der_spalte(self):
        from app.models.chat_message import ChatMessage
        from app.services.chat_persistence import upsert_chat_message
        from sqlalchemy import select

        with patch("app.db.session.async_session_factory", self.Session), \
                patch("app.services.chat_persistence.async_session_factory", self.Session):
            await upsert_chat_message("k1", "s1", "m1", "assistant", content="fertig",
                                      meta={"cost_usd": 0.0}, cost_usd=0.0,
                                      input_tokens=1_000_000, output_tokens=100_000)
        async with self.Session() as db:
            zeile = (await db.execute(select(ChatMessage))).scalar_one()
        self.assertAlmostEqual(zeile.cost_usd, 3.0)
        self.assertAlmostEqual(zeile.meta["cost_usd"], 3.0)
