"""„Zuletzt aktiv“ und „vom Nutzer gestoppt“ in den Agent-Antworten (Listenansicht).

* ``agents.last_active_at`` wird gesetzt, wenn eine Aufgabe startet oder endet,
  der Agent im Chat antwortet und eine Kanal-Nachricht an ihn geht — gedrosselt
  auf hoechstens einen Schreibvorgang je Agent und Minute.
* ``stopped_by_user`` kommt aus dem vorhandenen Halt-Flag (core/agent_duty) und
  unterscheidet „Gestoppt“ vom Leerlauf-Stopp („Schläft“).

Das Schreiben laeuft gegen ECHTES SQL (in-memory SQLite): die Drossel soll
Datenbank-Schreibvorgaenge sparen, ein Fake-Stub wuerde genau das wegtesten.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import agent_aktivitaet as akt
from app.core.agent_duty import nutzerhalt_setzen
from app.models.agent import Agent, AgentState
from app.models.user import User
from app.schemas.agent import AgentResponse

JETZT = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


class _RedisClient:
    """SET NX mit Ablauf — ``vergehen`` laesst die Zeit fuer die Schluessel laufen."""

    def __init__(self):
        self.schluessel: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.schluessel:
            return None
        self.schluessel[key] = value
        return True

    def vergehen(self):
        self.schluessel.clear()


class _Redis:
    def __init__(self):
        self.client = _RedisClient()


class _KaputtesRedis:
    class client:  # noqa: N801
        @staticmethod
        async def set(*_a, **_k):
            raise ConnectionError("redis weg")


class DrosselTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (User, Agent):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.angelegt = JETZT - timedelta(days=3)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="A1", state=AgentState.IDLE, config={},
                         created_at=self.angelegt, updated_at=self.angelegt))
            await db.commit()
        p = patch("app.db.session.async_session_factory", self.Session)
        p.start()
        self.addCleanup(p.stop)
        akt._lokal_zuletzt.clear()
        self.addCleanup(akt._lokal_zuletzt.clear)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _agent(self) -> Agent:
        async with self.Session() as db:
            return await db.get(Agent, "a1")

    @staticmethod
    def _utc(wert: datetime) -> datetime:
        # SQLite liefert ohne Zeitzone zurueck
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)

    async def test_setzt_den_zeitpunkt(self):
        self.assertTrue(await akt.aktivitaet_vermerken(_Redis(), "a1", JETZT))
        agent = await self._agent()
        self.assertEqual(self._utc(agent.last_active_at), JETZT)
        # „Einstellungen geaendert“ bleibt, wo es war.
        self.assertEqual(self._utc(agent.updated_at), self.angelegt)

    async def test_hoechstens_einmal_je_minute(self):
        redis = _Redis()
        self.assertTrue(await akt.aktivitaet_vermerken(redis, "a1", JETZT))
        später = JETZT + timedelta(seconds=30)
        self.assertFalse(await akt.aktivitaet_vermerken(redis, "a1", später))
        self.assertEqual(self._utc((await self._agent()).last_active_at), JETZT)
        # Nach Ablauf des Fensters (Redis-TTL) wird wieder geschrieben.
        redis.client.vergehen()
        self.assertTrue(await akt.aktivitaet_vermerken(redis, "a1", später))
        self.assertEqual(self._utc((await self._agent()).last_active_at), später)

    async def test_drossel_gilt_je_agent(self):
        redis = _Redis()
        await akt.aktivitaet_vermerken(redis, "a1", JETZT)
        self.assertTrue(await akt.aktivitaet_vermerken(redis, "a2", JETZT))

    async def test_ohne_redis_drosselt_prozesslokal(self):
        for redis in (None, _KaputtesRedis()):
            akt._lokal_zuletzt.clear()
            self.assertTrue(await akt.aktivitaet_vermerken(redis, "a1", JETZT))
            self.assertFalse(await akt.aktivitaet_vermerken(redis, "a1", JETZT))

    async def test_datenbankfehler_bricht_nichts_ab(self):
        kaputt = MagicMock(side_effect=RuntimeError("db weg"))
        with patch("app.db.session.async_session_factory", kaputt):
            self.assertFalse(await akt.aktivitaet_vermerken(_Redis(), "a1", JETZT))

    async def test_ohne_agent_nichts(self):
        self.assertFalse(await akt.aktivitaet_vermerken(_Redis(), None))
        self.assertFalse(await akt.aktivitaet_vermerken(_Redis(), ""))


class AusloeserTests(unittest.IsolatedAsyncioTestCase):
    """Die vier Anlaesse aus der Spezifikation rufen die gedrosselte Markierung auf."""

    async def test_kanal_nachricht(self):
        from app.services import user_lifecycle as ul

        with patch("app.core.agent_aktivitaet.aktivitaet_vermerken", AsyncMock()) as vermerken:
            redis = _Redis()
            await ul.mark_agent_interaction(redis, "a1", JETZT)
        vermerken.assert_awaited_once_with(redis, "a1", JETZT)

    def _router(self, task):
        from app.core.task_router import TaskRouter

        ergebnis = MagicMock()
        ergebnis.scalar_one_or_none.return_value = task
        db = SimpleNamespace(execute=AsyncMock(return_value=ergebnis), commit=AsyncMock())
        return TaskRouter(db, _Redis(), MagicMock())

    async def test_aufgabe_startet(self):
        from app.models.task import TaskStatus

        task = SimpleNamespace(id="t1", status=TaskStatus.QUEUED, agent_id="a1", title="T",
                               prompt="p", priority=1, model=None)
        router = self._router(task)
        with patch("app.core.task_router.aktivitaet_vermerken", AsyncMock()) as vermerken, \
             patch("app.services.job_state.checkpoint", AsyncMock()):
            await router.handle_task_start({"task_id": "t1", "agent_id": "a1"})
        vermerken.assert_awaited_once_with(router.redis, "a1")

    async def test_aufgabe_endet(self):
        from app.core.task_router import TaskRouter
        from app.models.task import TaskStatus

        task = SimpleNamespace(id="t1", status=TaskStatus.RUNNING, agent_id="a1", result=None,
                               error=None, retain=True, metadata_={})
        router = self._router(task)
        # Nur der Anfang interessiert — danach steigt der Lauf gezielt aus.
        with patch("app.core.task_router.aktivitaet_vermerken", AsyncMock()) as vermerken, \
             patch.object(TaskRouter, "_update_agent_metrics", AsyncMock(side_effect=_Ende)):
            with self.assertRaises(_Ende):
                await router.handle_task_completion(
                    {"task_id": "t1", "agent_id": "a1", "status": "failed", "error": "x"})
        vermerken.assert_awaited_once_with(router.redis, "a1")

    async def test_chat_antwort_nur_mit_belastbarer_kennung(self):
        """Auf dem Sammelkanal kann sich jeder Agent als ein anderer ausgeben —
        ohne passende Nutzernachricht wird dort nichts vermerkt."""
        import app.main as main

        class _Db:
            scalar = AsyncMock(return_value=None)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        done = {"agent_id": "a1", "message_id": "m1", "data": {"text": "x"}}
        with patch("app.db.session.async_session_factory", _Db), \
             patch("app.core.agent_aktivitaet.aktivitaet_vermerken", AsyncMock()) as vermerken:
            await main._persist_chat_completion(_Redis(), done, kennung_vertraut=False)
            vermerken.assert_not_awaited()
            with patch.object(main, "_kosten_ohne_verlauf", AsyncMock()):
                await main._persist_chat_completion(_Redis(), done, kennung_vertraut=True)
            vermerken.assert_awaited_once()
            self.assertEqual(vermerken.await_args.args[1], "a1")


class _Ende(Exception):
    pass


class AntwortFelderTests(unittest.IsolatedAsyncioTestCase):
    def test_antwortmodell_kennt_die_felder(self):
        basis = dict(id="a1", name="A", container_id=None, state=AgentState.STOPPED, model="m",
                     created_at=JETZT, updated_at=JETZT)
        leer = AgentResponse(**basis).model_dump(mode="json")
        self.assertIsNone(leer["last_active_at"])
        self.assertFalse(leer["stopped_by_user"])
        voll = AgentResponse(**basis, last_active_at=JETZT, stopped_by_user=True,
                             image_outdated=True).model_dump(mode="json")
        self.assertEqual(voll["last_active_at"], "2026-10-07T12:00:00Z")
        self.assertTrue(voll["stopped_by_user"])
        self.assertTrue(voll["image_outdated"])

    async def _metriken(self, agent):
        from app.core.agent_manager import AgentManager

        manager = AgentManager.__new__(AgentManager)
        manager._get_agent = AsyncMock(return_value=agent)
        manager.docker = MagicMock()
        manager.redis = SimpleNamespace(get_agent_status=AsyncMock(return_value={}),
                                        get_queue_depth=AsyncMock(return_value=0))
        manager._wartende_gespraeche = AsyncMock(return_value=[])

        class _Sitzung:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        kosten = SimpleNamespace(gesamt=0.0)
        with patch("app.db.session.async_session_factory", _Sitzung), \
             patch("app.core.kosten.kosten", AsyncMock(return_value=kosten)):
            return await manager.get_agent_with_metrics(agent.id, include_stats=False)

    def _agent(self, **mehr):
        werte = dict(id="a1", name="A", container_id=None, state=AgentState.STOPPED, model="m",
                     mode="claude_code", config={}, access_policy={}, autonomy_level="l3",
                     llm_config=None, ai_account_id=None, budget_usd=None,
                     budget_exceeded_action="stop", browser_mode=False, webhook_enabled=False,
                     webhook_token=None, user_id="u1", created_at=JETZT, updated_at=JETZT,
                     shared_for_rooms=False, favorite=False, last_active_at=None)
        werte.update(mehr)
        return Agent(**werte)

    async def test_agent_ohne_container_gestoppt_vom_nutzer(self):
        """Wie in den Abnahmetests: direkt angelegt, kein Container, Zustand stopped."""
        agent = self._agent(last_active_at=JETZT)
        nutzerhalt_setzen(agent, True)
        antwort = AgentResponse(**await self._metriken(agent)).model_dump(mode="json")
        self.assertTrue(antwort["stopped_by_user"])
        self.assertEqual(antwort["last_active_at"], "2026-10-07T12:00:00Z")
        self.assertEqual(antwort["state"], "stopped")

    async def test_automatischer_stopp_ist_nicht_vom_nutzer(self):
        antwort = AgentResponse(**await self._metriken(self._agent())).model_dump(mode="json")
        self.assertFalse(antwort["stopped_by_user"])
        self.assertIsNone(antwort["last_active_at"])


if __name__ == "__main__":
    unittest.main()
