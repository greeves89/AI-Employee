"""Keine ungefragten Zeitpläne: System-Zeitpläne nur mit Verantwortungsbereichen (#913).

Jeder neue Agent bekam ungefragt drei aktive Zeitpläne ([Proactive] stündlich,
[Rhythmus] 21:30 und 07:00). Ohne Verantwortungsbereiche wurde jeder dieser Läufe
übersprungen — keine Wirkung, aber Wecken, Hinweise und Verwirrung.

Jetzt gilt EINE Invariante, an EINER Stelle (``core/eigeninitiative.abgleichen``):
System-Zeitpläne existieren genau dann, wenn der Agent Verantwortungsbereiche hat und
seine Eigeninitiative an ist. Geprüft wird am echten Ablauf gegen eine Datenbank —
über alle Wege, auf denen Bereiche entstehen oder verschwinden.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import DateTime, select
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.types import TypeDecorator

from app.core import eigeninitiative
from app.models.agent import Agent, AgentState
from app.models.agent_template import AgentTemplate
from app.models.notification import Notification
from app.models.schedule import Schedule

UTC = timezone.utc


class _UTCDateTime(TypeDecorator):
    """SQLite verliert die Zeitzone — wie in test_ensure_planning_rhythm_cleanup.py."""

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


SQLiteDialect_pysqlite.colspecs = {
    **SQLiteDialect_pysqlite.colspecs,
    DateTime: _UTCDateTime,
}

BEREICHE = [{"title": "Posteingang sichten", "rhythm": "daily"}]


def _cfg(bereiche=None, enabled=True):
    proactive = {"enabled": enabled, "interval_seconds": 3600}
    if bereiche is not None:
        proactive["responsibilities"] = bereiche
    return {"role": "Assistenz", "proactive": proactive}


class _DbBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Schedule, Notification, AgentTemplate):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _agent(self, agent_id="a1", config=None):
        async with self.Session() as db:
            db.add(Agent(id=agent_id, name=f"Agent {agent_id}", state=AgentState.RUNNING,
                         config=config if config is not None else _cfg()))
            await db.commit()

    async def _zeitplaene(self, agent_id="a1") -> list[Schedule]:
        async with self.Session() as db:
            return list((await db.execute(
                select(Schedule).where(Schedule.agent_id == agent_id)
            )).scalars().all())

    async def _system(self, agent_id="a1") -> list[str]:
        return sorted(s.name for s in await self._zeitplaene(agent_id)
                      if s.name.startswith(("[Proactive]", "[Rhythmus]")))

    async def _hinweise(self, agent_id="a1") -> int:
        async with self.Session() as db:
            return len((await db.execute(
                select(Notification).where(Notification.agent_id == agent_id)
            )).scalars().all())

    async def _abgleichen(self, agent_id="a1", **kw):
        async with self.Session() as db:
            agent = await db.get(Agent, agent_id)
            ergebnis = await eigeninitiative.abgleichen(db, agent, **kw)
            await db.commit()
            return ergebnis

    async def _config(self, agent_id="a1") -> dict:
        async with self.Session() as db:
            return dict((await db.get(Agent, agent_id)).config or {})

    async def _setze_config(self, config, agent_id="a1"):
        async with self.Session() as db:
            agent = await db.get(Agent, agent_id)
            agent.config = config
            await db.commit()


class Abgleichen(_DbBasis):
    """MCDC: Bereiche vorhanden? | Eigeninitiative an? -> Zeitpläne ja/nein."""

    async def test_ohne_bereiche_keine_zeitplaene(self):
        await self._agent(config=_cfg(bereiche=None))
        await self._abgleichen()
        self.assertEqual(await self._system(), [])

    async def test_mit_bereichen_eins_plus_zwei(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        await self._abgleichen()
        namen = await self._system()
        self.assertEqual(len(namen), 3)
        self.assertEqual(sum(n.startswith("[Proactive]") for n in namen), 1)
        self.assertIn("[Rhythmus] Abendplanung", namen)
        self.assertIn("[Rhythmus] Morgencheck", namen)
        proaktiv = [s for s in await self._zeitplaene() if s.name.startswith("[Proactive]")][0]
        self.assertTrue(proaktiv.enabled)
        self.assertEqual(proaktiv.interval_seconds, 3600)
        self.assertEqual((await self._config())["proactive"]["schedule_id"], proaktiv.id)

    async def test_bereiche_aber_eigeninitiative_aus(self):
        await self._agent(config=_cfg(bereiche=BEREICHE, enabled=False))
        await self._abgleichen()
        self.assertEqual(await self._system(), [])

    async def test_zweiter_abgleich_legt_nichts_doppelt_an(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        await self._abgleichen()
        await self._abgleichen()
        self.assertEqual(len(await self._system()), 3)
        self.assertEqual(await self._hinweise(), 1, "Der Hinweis kommt einmal, nicht bei jedem Abgleich.")

    async def test_hinweis_wenn_bereiche_dazukommen(self):
        await self._agent(config=_cfg(bereiche=None))
        await self._abgleichen()
        self.assertEqual(await self._hinweise(), 0)
        await self._setze_config(_cfg(bereiche=BEREICHE))
        await self._abgleichen()
        self.assertEqual(await self._hinweise(), 1)

    async def test_ohne_hinweis_wenn_abgeschaltet(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        await self._abgleichen(hinweis=False)
        self.assertEqual(len(await self._system()), 3)
        self.assertEqual(await self._hinweise(), 0)

    async def test_bereiche_leeren_entfernt_sie_aber_nicht_eigene_zeitplaene(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        async with self.Session() as db:
            db.add(Schedule(id="eigen", name="Wochenbericht", prompt="x", interval_seconds=0,
                            cron_expression="0 9 * * 1", agent_id="a1", enabled=True,
                            next_run_at=datetime.now(UTC) + timedelta(days=1)))
            await db.commit()
        await self._abgleichen()
        await self._setze_config(_cfg(bereiche=[]))
        await self._abgleichen()
        self.assertEqual(await self._system(), [])
        self.assertEqual([s.id for s in await self._zeitplaene()], ["eigen"])
        self.assertNotIn("schedule_id", (await self._config())["proactive"])

    async def test_abgeschalteter_rhythmus_bleibt_aus(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        await self._abgleichen()
        async with self.Session() as db:
            abend = (await db.execute(select(Schedule).where(
                Schedule.name == "[Rhythmus] Abendplanung"))).scalar_one()
            abend.enabled = False
            await db.commit()
        await self._abgleichen()
        async with self.Session() as db:
            abend = (await db.execute(select(Schedule).where(
                Schedule.name == "[Rhythmus] Abendplanung"))).scalar_one()
        self.assertFalse(abend.enabled, "Vom Nutzer abgeschaltet bleibt abgeschaltet.")

    async def test_eigeninitiative_wieder_an_schaltet_proaktiv_ein(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        async with self.Session() as db:
            db.add(Schedule(id="alt", name="[Proactive] Agent a1", prompt="x", interval_seconds=3600,
                            agent_id="a1", enabled=False, next_run_at=datetime.now(UTC)))
            await db.commit()
        await self._abgleichen()
        proaktiv = [s for s in await self._zeitplaene() if s.name.startswith("[Proactive]")]
        self.assertEqual([s.id for s in proaktiv], ["alt"], "Vorhandener Zeitplan wird übernommen.")
        self.assertTrue(proaktiv[0].enabled)
        self.assertGreater(proaktiv[0].next_run_at, datetime.now(UTC))


class Migration(_DbBasis):
    """Startup: System-Zeitpläne von Agenten ohne Bereiche entfernen, Rest unberührt."""

    async def test_entfernt_nur_bei_agenten_ohne_bereiche(self):
        jetzt = datetime.now(UTC)
        await self._agent("ohne", config=_cfg(bereiche=None))
        await self._agent("mit", config=_cfg(bereiche=BEREICHE))
        async with self.Session() as db:
            for aid in ("ohne", "mit"):
                db.add(Schedule(id=f"p-{aid}", name=f"[Proactive] {aid}", prompt="x",
                                interval_seconds=3600, agent_id=aid, enabled=True, next_run_at=jetzt))
                db.add(Schedule(id=f"r-{aid}", name="[Rhythmus] Abendplanung", prompt="x",
                                interval_seconds=0, cron_expression="30 21 * * *", agent_id=aid,
                                enabled=True, next_run_at=jetzt))
            db.add(Schedule(id="eigen", name="Wochenbericht", prompt="x", interval_seconds=3600,
                            agent_id="ohne", enabled=True, next_run_at=jetzt))
            await db.commit()

        async with self.Session() as db:
            entfernt = await eigeninitiative.ohne_bereiche_aufraeumen(db)

        self.assertEqual(entfernt, 2)
        self.assertEqual([s.id for s in await self._zeitplaene("ohne")], ["eigen"])
        # Agent mit Bereichen: exakt dieselben Zeilen, nichts neu angelegt.
        self.assertEqual(sorted(s.id for s in await self._zeitplaene("mit")), ["p-mit", "r-mit"])
        self.assertEqual(await self._hinweise("ohne") + await self._hinweise("mit"), 0)


class VorlageUndEinrichtung(_DbBasis):
    """Die Wege, auf denen Bereiche entstehen: Vorlage, Einrichtung, Einstellungen."""

    async def test_vorlage_ohne_bereiche_keine_zeitplaene(self):
        from app.api.templates import vorlage_anwenden
        await self._agent(config=_cfg(bereiche=None))
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            await vorlage_anwenden(agent, AgentTemplate(id=1, name="social", display_name="Social Media",
                                                        responsibilities=[]), db, MagicMock())
        self.assertEqual(await self._system(), [])

    async def test_vorlage_mit_bereichen_drei_zeitplaene_ohne_hinweis(self):
        from app.api.templates import vorlage_anwenden
        await self._agent(config=_cfg(bereiche=None))
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            await vorlage_anwenden(agent, AgentTemplate(id=1, name="assistenz", display_name="Assistenz",
                                                        responsibilities=BEREICHE), db, MagicMock())
        self.assertEqual(len(await self._system()), 3)
        self.assertEqual(await self._hinweise(), 0, "Beim Anlegen zeigt der Dialog es schon an.")

    async def test_einrichtung_im_chat_legt_sie_an_und_meldet_es(self):
        from app.api import onboarding as onboarding_api
        await self._agent(config=_cfg(bereiche=None))
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            with patch.object(onboarding_api, "_agent_for", AsyncMock(return_value=agent)):
                await onboarding_api.complete_onboarding(
                    "a1", onboarding_api.OnboardingCompletion(responsibilities=BEREICHE),
                    user=SimpleNamespace(id="a1"), db=db,
                )
        self.assertEqual(len(await self._system()), 3)
        self.assertEqual(await self._hinweise(), 1)

    async def _einstellungen(self, **felder):
        from app.api import agents as agents_api
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            manager = SimpleNamespace(_get_agent=AsyncMock(return_value=agent))
            with patch.object(agents_api, "_check_owner", AsyncMock()):
                return await agents_api.update_proactive_config(
                    "a1", agents_api.ProactiveUpdate(**felder),
                    user=SimpleNamespace(id="u1"), db=db, manager=manager,
                )

    async def test_einstellungen_setzen_und_leeren(self):
        await self._agent(config=_cfg(bereiche=None))
        await self._einstellungen(enabled=True, responsibilities=BEREICHE)
        self.assertEqual(len(await self._system()), 3)
        antwort = await self._einstellungen(enabled=True, responsibilities=[])
        self.assertEqual(await self._system(), [])
        self.assertNotIn("schedule_id", antwort["proactive"])

    async def test_einstellungen_ausschalten_entfernt_sie(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        await self._abgleichen()
        await self._einstellungen(enabled=False)
        self.assertEqual(await self._system(), [])
        self.assertEqual((await self._config())["proactive"]["responsibilities"][0]["title"],
                         "Posteingang sichten", "Ausschalten löscht die Bereiche nicht.")

    async def test_einstellungen_intervall_wird_uebernommen(self):
        await self._agent(config=_cfg(bereiche=BEREICHE))
        await self._einstellungen(enabled=True, interval_seconds=7200)
        proaktiv = [s for s in await self._zeitplaene() if s.name.startswith("[Proactive]")]
        self.assertEqual([s.interval_seconds for s in proaktiv], [7200])


class NeuerAgent(unittest.IsolatedAsyncioTestCase):
    """``AgentManager.create_agent`` legt keinen Zeitplan mehr selbst an."""

    async def test_anlegen_ohne_bereiche_kein_zeitplan(self):
        from app.core.agent_manager import AgentManager

        docker = MagicMock()
        docker.create_container.return_value = SimpleNamespace(id="neuer-container")
        db = MagicMock()
        db.get = AsyncMock(return_value=None)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        db.flush = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.scalar = AsyncMock(return_value=None)
        m = AgentManager(db, docker, MagicMock())
        m._publish_event = AsyncMock()
        m._agent_redis_url = AsyncMock(return_value="redis://redis:6379")
        m._get_secrets_env = AsyncMock(return_value={})
        m._effective_llm_config = AsyncMock(return_value=None)
        m._get_custom_mcp_env = AsyncMock(return_value={})
        m._get_integration_env = AsyncMock(return_value={})
        m._owner_credential_env = AsyncMock(return_value={})
        m._apply_permissions = MagicMock()
        m._update_team_registry = MagicMock()
        with patch("app.core.mounts.get_effective_catalog", AsyncMock(return_value={})), \
             patch("app.core.master_rules.load", AsyncMock(return_value="")), \
             patch("app.services.claude_token_service.ClaudeTokenService",
                   MagicMock(return_value=MagicMock(refresh_access_token=AsyncMock()))):
            agent = await m.create_agent(name="Ben", user_id="u1")

        hinzugefuegt = [c.args[0] for c in db.add.call_args_list]
        self.assertFalse([o for o in hinzugefuegt if isinstance(o, Schedule)],
                         "Ein neuer Agent ohne Bereiche bekommt keinen Zeitplan.")
        # Die Eigeninitiative ist trotzdem an — sie greift, sobald Bereiche kommen.
        self.assertTrue(agent.config["proactive"]["enabled"])
        self.assertNotIn("schedule_id", agent.config["proactive"])


if __name__ == "__main__":
    unittest.main()
