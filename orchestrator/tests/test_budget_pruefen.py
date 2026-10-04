"""Budget gilt auch im Chat, Standard Stopp, Sparmodus laufzeitgerecht (#898).

Befund: Der Chat lief am Budget vorbei (nur Aufgaben wurden geprüft), der
Sparmodus zwang Codex- und Custom-LLM-Agenten ein Claude-Modell auf, das sie
gar nicht ausführen können (die Modellprüfung setzte es stillschweigend wieder
zurück — das Budget war wirkungslos), und Vorgabe war „auf Haiku umschalten".

Geprüft wird die Entscheidung gegen eine echte (SQLite-)Datenbank.
"""

import inspect
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import budget, model_catalog
from app.models.agent import Agent, AgentState
from app.models.chat_message import ChatMessage
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole

JETZT = datetime.now(timezone.utc)


class BudgetBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001 — schon registriert
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent, Task, ChatMessage, Notification):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.einstellungen = patch.multiple(
            budget.settings, platform_budget_usd=0.0, budget_gilt_fuer_chat=True,
            model_provider="anthropic", display_currency="USD", create=True,
        )
        self.einstellungen.start()

    async def asyncTearDown(self):
        self.einstellungen.stop()
        await self.engine.dispose()

    async def agent(self, **felder) -> Agent:
        werte = dict(id="a1", name="Agent", state=AgentState.IDLE, user_id="u1",
                     config={}, mode="claude_code")
        werte.update(felder)
        async with self.Session() as db:
            if not await db.get(User, "u1"):
                db.add(User(id="u1", email="u1@example.invalid", name="U", role=UserRole.MEMBER))
            agent = Agent(**werte)
            db.add(agent)
            await db.commit()
        return agent

    async def kosten(self, agent_id="a1", aufgabe=0.0, chat=0.0):
        async with self.Session() as db:
            if aufgabe:
                db.add(Task(id=f"t-{agent_id}-{aufgabe}", title="t", prompt="p",
                            status=TaskStatus.COMPLETED, agent_id=agent_id,
                            cost_usd=aufgabe, created_at=JETZT))
            if chat:
                db.add(ChatMessage(agent_id=agent_id, session_id="s", message_id=f"m{chat}",
                                   role="assistant", content="x", cost_usd=chat, timestamp=JETZT))
            await db.commit()

    async def pruefen(self, agent_id="a1", fuer_chat=False):
        async with self.Session() as db:
            agent = await db.get(Agent, agent_id)
            return await budget.budget_pruefen(db, agent, fuer_chat=fuer_chat)


class Entscheidung(BudgetBasis):
    async def test_ohne_budget_erlaubt(self):
        await self.agent(budget_usd=None)
        await self.kosten(aufgabe=100)
        e = await self.pruefen()
        self.assertEqual(e.art, budget.ERLAUBEN)

    async def test_unter_budget_erlaubt(self):
        await self.agent(budget_usd=10)
        await self.kosten(aufgabe=2, chat=3)
        self.assertEqual((await self.pruefen()).art, budget.ERLAUBEN)

    async def test_chatkosten_zaehlen_mit(self):
        """Bisher prüfte das Budget nur Aufgaben — 100 % Chatkosten blieben unsichtbar."""
        await self.agent(budget_usd=10, budget_exceeded_action="stop")
        await self.kosten(aufgabe=4, chat=6)
        e = await self.pruefen()
        self.assertEqual(e.art, budget.BLOCKIEREN)
        self.assertIn("Budget", e.hinweis)
        self.assertIn("aufgebraucht", e.hinweis)

    async def test_vormonat_zaehlt_nicht(self):
        await self.agent(budget_usd=10, budget_exceeded_action="stop")
        async with self.Session() as db:
            db.add(Task(id="alt", title="t", prompt="p", status=TaskStatus.COMPLETED,
                        agent_id="a1", cost_usd=50,
                        created_at=JETZT.replace(day=1) - timedelta(days=2)))
            await db.commit()
        self.assertEqual((await self.pruefen()).art, budget.ERLAUBEN)

    async def test_nutzerbudget(self):
        await self.agent(budget_usd=None)
        async with self.Session() as db:
            (await db.get(User, "u1")).budget_usd = 5
            await db.commit()
        await self.kosten(chat=6)
        self.assertEqual((await self.pruefen()).art, budget.BLOCKIEREN)

    async def test_anlagenbudget(self):
        await self.agent(budget_usd=None)
        await self.kosten(aufgabe=9)
        with patch.object(budget.settings, "platform_budget_usd", 5.0):
            self.assertEqual((await self.pruefen()).art, budget.BLOCKIEREN)


class Sparmodus(BudgetBasis):
    async def test_claude_bekommt_das_sparmodell_aus_dem_katalog(self):
        await self.agent(budget_usd=1, budget_exceeded_action="haiku")
        await self.kosten(aufgabe=2)
        e = await self.pruefen()
        self.assertEqual(e.art, budget.SPARMODELL)
        self.assertEqual(e.modell, model_catalog.sparmodell_fuer("claude_code", "anthropic"))
        self.assertTrue(model_catalog.is_model_allowed_for_mode("claude_code", e.modell))

    async def test_codex_wird_nicht_auf_haiku_gezwungen(self):
        """Codex kennt kein günstigeres Modell — dann greift Stopp, nicht ein
        Claude-Modell, das die Laufzeit zurücksetzt."""
        await self.agent(budget_usd=1, budget_exceeded_action="haiku", mode="codex_cli")
        await self.kosten(aufgabe=2)
        e = await self.pruefen()
        self.assertEqual(e.art, budget.BLOCKIEREN)
        self.assertIsNone(e.modell)

    async def test_custom_llm_wird_nicht_auf_haiku_gezwungen(self):
        await self.agent(budget_usd=1, budget_exceeded_action="haiku", mode="custom_llm")
        await self.kosten(aufgabe=2)
        self.assertEqual((await self.pruefen()).art, budget.BLOCKIEREN)

    async def test_claude_ueber_codex_anbieter_zaehlt_als_codex(self):
        await self.agent(budget_usd=1, budget_exceeded_action="haiku",
                         config={"model_provider": "codex"})
        await self.kosten(aufgabe=2)
        self.assertEqual((await self.pruefen()).art, budget.BLOCKIEREN)

    def test_katalog(self):
        self.assertIsNone(model_catalog.sparmodell_fuer("codex_cli", "codex"))
        self.assertIsNone(model_catalog.sparmodell_fuer("custom_llm", "openai"))
        for anbieter in ("anthropic", "bedrock", "vertex", "foundry"):
            modell = model_catalog.sparmodell_fuer("claude_code", anbieter)
            self.assertIn("haiku", modell)


class Chatschalter(BudgetBasis):
    async def test_chat_wird_geprueft(self):
        await self.agent(budget_usd=1, budget_exceeded_action="stop")
        await self.kosten(chat=2)
        self.assertEqual((await self.pruefen(fuer_chat=True)).art, budget.BLOCKIEREN)

    async def test_schalter_aus_laesst_den_chat_durch(self):
        await self.agent(budget_usd=1, budget_exceeded_action="stop")
        await self.kosten(chat=2)
        with patch.object(budget.settings, "budget_gilt_fuer_chat", False):
            self.assertEqual((await self.pruefen(fuer_chat=True)).art, budget.ERLAUBEN)
            # Aufgaben bleiben geprüft
            self.assertEqual((await self.pruefen()).art, budget.BLOCKIEREN)

    def test_schalter_ist_speicherbar(self):
        """Ohne Eintrag in ALLOWED_KEYS scheitert das Speichern still."""
        from app.api.settings import _FIELD_MAP
        from app.config import Settings
        from app.schemas.settings import SettingsResponse, SettingsUpdate
        from app.services.settings_service import ALLOWED_KEYS

        self.assertIn("budget_gilt_fuer_chat", ALLOWED_KEYS)
        self.assertIn("budget_gilt_fuer_chat", _FIELD_MAP)
        self.assertTrue(Settings.model_fields["budget_gilt_fuer_chat"].default)
        self.assertIn("budget_gilt_fuer_chat", SettingsUpdate.model_fields)
        self.assertIn("budget_gilt_fuer_chat", SettingsResponse.model_fields)


class AnlagenbudgetOhneBetraege(BudgetBasis):
    """Die Anlagengrenze verrät Mitgliedern nicht die Gesamtkosten der Installation.

    Der Hinweis geht an jeden, der chattet — auch an Mitglieder. Beträge sehen nur
    Administratoren (ihre Benachrichtigung, Verwaltung → Budget)."""

    async def _gesperrt(self):
        await self.agent(budget_usd=None, budget_exceeded_action="stop")
        await self.kosten(aufgabe=9, chat=3)
        with patch.object(budget.settings, "platform_budget_usd", 5.0):
            async with self.Session() as db:
                agent = await db.get(Agent, "a1")
                e = await budget.budget_pruefen(db, agent, fuer_chat=True)
                await budget.sperre_melden(db, agent, e)
                meldungen = (await db.execute(select(Notification))).scalars().all()
        return e, meldungen

    async def test_hinweis_ohne_betraege(self):
        e, _ = await self._gesperrt()
        self.assertEqual(e.grenze, "anlage")
        self.assertEqual(e.hinweis, budget.HINWEIS_ANLAGE)
        self.assertNotRegex(e.hinweis, r"\d")
        self.assertIn("Administrator", e.hinweis)

    async def test_besitzer_meldung_ohne_betraege_admin_mit(self):
        _, meldungen = await self._gesperrt()
        an_besitzer = [m for m in meldungen if m.agent_id == "a1"]
        an_admins = [m for m in meldungen if m.agent_id == "system"]
        self.assertEqual(len(an_besitzer), 1)
        self.assertNotRegex(an_besitzer[0].message, r"\d")
        self.assertRegex(an_admins[0].message, r"12,00")

    async def test_aufgabenweg_ohne_betraege(self):
        """Auch wer eine Aufgabe anlegt, erfährt die Gesamtkosten der Anlage nicht."""
        from app.core.task_router import TaskRouter

        await self.agent(budget_usd=None)
        await self.kosten(aufgabe=9, chat=3)
        with patch.object(budget.settings, "platform_budget_usd", 5.0):
            async with self.Session() as db:
                router = TaskRouter.__new__(TaskRouter)
                router.db = db
                with self.assertRaises(ValueError) as fehler:
                    await router._check_platform_budget()
        self.assertNotRegex(str(fehler.exception), r"\d")
        self.assertIn("Administrator", str(fehler.exception))

    async def test_agentenbudget_nennt_weiter_betraege(self):
        """Das eigene Agentenbudget ist kein Geheimnis — dort bleiben die Zahlen."""
        await self.agent(budget_usd=10, budget_exceeded_action="stop")
        await self.kosten(chat=12)
        e = await self.pruefen(fuer_chat=True)
        self.assertRegex(e.hinweis, r"12,00")


class AdminWirdBenachrichtigt(BudgetBasis):
    async def test_einmal_je_agent_und_monat(self):
        await self.agent(budget_usd=1, budget_exceeded_action="stop")
        await self.kosten(chat=2)
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            e = await budget.budget_pruefen(db, agent, fuer_chat=True)
            self.assertTrue(await budget.sperre_melden(db, agent, e))
            self.assertFalse(await budget.sperre_melden(db, agent, e))
            meldungen = (await db.execute(select(Notification))).scalars().all()
        an_admins = [m for m in meldungen if m.agent_id == "system"]
        an_besitzer = [m for m in meldungen if m.agent_id == "a1"]
        self.assertEqual(len(an_admins), 1)
        self.assertEqual(len(an_besitzer), 1)
        self.assertIn("Budget", an_admins[0].title)


class VorgabeStopp(unittest.TestCase):
    """Neuer Agent → Stopp, an allen Stellen (Modell, Anlage, Schemas, Vorlage)."""

    def test_schemas(self):
        from app.api.templates import CreateFromTemplate
        from app.schemas.agent import AgentCreate, AgentResponse

        self.assertEqual(AgentCreate(name="x").budget_exceeded_action, "stop")
        self.assertEqual(AgentResponse.model_fields["budget_exceeded_action"].default, "stop")
        self.assertEqual(CreateFromTemplate().budget_exceeded_action, "stop")

    def test_modell_und_anlage(self):
        from app.core.agent_manager import AgentManager

        spalte = Agent.__table__.c.budget_exceeded_action
        self.assertEqual(spalte.default.arg, "stop")
        self.assertEqual(spalte.server_default.arg, "stop")
        param = inspect.signature(AgentManager.create_agent).parameters["budget_exceeded_action"]
        self.assertEqual(param.default, "stop")


class Umstellung(BudgetBasis):
    """Bestand: ohne Budget → Stopp (wirkungsgleich); mit Budget + Sparmodus bleibt,
    bekommt aber einen Concierge-Hinweis."""

    async def test_umstellen(self):
        await self.agent(id="ohne", budget_usd=None, budget_exceeded_action="haiku")
        await self.agent(id="mit", budget_usd=10, budget_exceeded_action="haiku")
        await self.agent(id="stopp", budget_usd=10, budget_exceeded_action="stop")
        async with self.Session() as db:
            umgestellt, hinweise = await budget.stopp_vorgabe_umstellen(db)
            self.assertEqual((umgestellt, hinweise), (1, 1))
            self.assertEqual((await db.get(Agent, "ohne")).budget_exceeded_action, "stop")
            mit = await db.get(Agent, "mit")
            self.assertEqual(mit.budget_exceeded_action, "haiku")
            self.assertTrue(budget.hinweis_offen(mit))
            self.assertFalse(budget.hinweis_offen(await db.get(Agent, "stopp")))
            # zweiter Lauf ändert nichts mehr
            self.assertEqual(await budget.stopp_vorgabe_umstellen(db), (0, 0))


if __name__ == "__main__":
    unittest.main()
