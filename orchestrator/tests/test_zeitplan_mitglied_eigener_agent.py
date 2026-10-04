"""Zeitpläne eines Mitglieds: eigener Agent, sichtbar, löschbar (#901, Runde 2).

Befund: Als Mitglied bot der Zeitplan-Dialog nur „Automatisch zuweisen" an. Der
Zeitplan entstand mit ``agent_id = NULL`` und war danach für das Mitglied
unsichtbar — weder pausier- noch löschbar. Schlimmer: beim Feuern wählte der
Lastverteiler unter ALLEN Agenten der Anlage, der Auftrag eines Mitglieds konnte
also auf dem Agenten eines anderen Nutzers laufen.

Geprüft wird das Verhalten (echte Abfragen gegen eine SQLite-Datenbank, echter
Lastverteiler), nicht der Quelltext.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import schedules as api
from app.core.load_balancer import AgentMetrics, LoadBalancer
from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.schedule import Schedule
from app.models.user import User, UserRole
from app.schemas.schedule import ScheduleCreate

MITGLIED = SimpleNamespace(id="ua", role=UserRole.MEMBER)
ANDERES_MITGLIED = SimpleNamespace(id="ub", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)


def _metrik(agent_id: str, state: str = "idle") -> AgentMetrics:
    return AgentMetrics(agent_id=agent_id, state=state, queue_depth=0,
                        cpu_percent=0.0, memory_percent=0.0, healthy=True)


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[
                User.__table__, Agent.__table__, AgentAccess.__table__, Schedule.__table__,
            ])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="ua", email="a@example.invalid", name="A", role=UserRole.MEMBER),
                User(id="ub", email="b@example.invalid", name="B", role=UserRole.MEMBER),
                User(id="adm", email="c@example.invalid", name="C", role=UserRole.ADMIN),
                Agent(id="a1", name="Eigener", user_id="ua", config={}),
                Agent(id="b1", name="Fremder", user_id="ub", config={}),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _anlegen(self, user, metriken=(), **felder):
        daten = ScheduleCreate(name="Bericht", prompt="Bericht schreiben",
                               interval_seconds=3600, **felder)
        with patch.object(LoadBalancer, "_collect_metrics", AsyncMock(return_value=list(metriken))):
            async with self.Session() as db:
                return await api.create_schedule(daten, user=user, db=db, redis=MagicMock())

    async def _zeile(self, schedule_id: str) -> Schedule | None:
        async with self.Session() as db:
            return (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()


class AnlegenOhneAgent(_Basis):
    async def test_mitglied_automatisch_bekommt_den_eigenen_agenten(self):
        """Der eigene Agent ist gestoppt (Lastverteiler kennt ihn nicht als frei) —
        trotzdem landet der Zeitplan bei ihm, nicht bei NULL."""
        antwort = await self._anlegen(MITGLIED, metriken=[_metrik("a1", "stopped")])
        self.assertEqual(antwort.agent_id, "a1")
        self.assertEqual((await self._zeile(antwort.id)).created_by, "ua")

    async def test_automatisch_trifft_nie_einen_fremden_agenten(self):
        """Der fremde Agent ist frei, der eigene gestoppt: gewählt wird der eigene."""
        antwort = await self._anlegen(MITGLIED, metriken=[_metrik("b1", "idle"), _metrik("a1", "stopped")])
        self.assertEqual(antwort.agent_id, "a1")

    async def test_mitglied_ohne_eigenen_agenten_bekommt_400(self):
        async with self.Session() as db:
            (await db.get(Agent, "a1")).user_id = "ub"
            await db.commit()
        with self.assertRaises(HTTPException) as fehler:
            await self._anlegen(MITGLIED, metriken=[_metrik("b1")])
        self.assertEqual(fehler.exception.status_code, 400)
        async with self.Session() as db:
            self.assertEqual((await db.execute(select(Schedule))).scalars().all(), [])

    async def test_fremden_agenten_zuweisen_wird_abgewiesen(self):
        with self.assertRaises(HTTPException) as fehler:
            await self._anlegen(MITGLIED, agent_id="b1")
        self.assertEqual(fehler.exception.status_code, 403)

    async def test_admin_darf_automatisch_ohne_agent_anlegen(self):
        antwort = await self._anlegen(ADMIN)
        self.assertIsNone(antwort.agent_id)
        self.assertEqual((await self._zeile(antwort.id)).created_by, "adm")


class SichtbarUndLoeschbar(_Basis):
    """Ein Zeitplan ohne Agent gehört dem, der ihn angelegt hat."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        jetzt = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
        async with self.Session() as db:
            db.add_all([
                Schedule(id="s-eigen", name="eigen", prompt="p", interval_seconds=3600,
                         agent_id=None, created_by="ua", next_run_at=jetzt),
                Schedule(id="s-fremd", name="fremd", prompt="p", interval_seconds=3600,
                         agent_id=None, created_by="ub", next_run_at=jetzt),
                Schedule(id="s-agent", name="agent", prompt="p", interval_seconds=3600,
                         agent_id="a1", next_run_at=jetzt),
            ])
            await db.commit()

    async def test_mitglied_sieht_eigenen_zeitplan_ohne_agent_aber_keinen_fremden(self):
        async with self.Session() as db:
            liste = await api.list_schedules(user=MITGLIED, db=db)
        self.assertEqual({s.id for s in liste.schedules}, {"s-eigen", "s-agent"})

    async def test_mitglied_pausiert_und_loescht_eigenen_zeitplan_ohne_agent(self):
        async with self.Session() as db:
            await api.pause_schedule("s-eigen", user=MITGLIED, db=db)
        self.assertFalse((await self._zeile("s-eigen")).enabled)
        async with self.Session() as db:
            await api.delete_schedule("s-eigen", user=MITGLIED, db=db)
        self.assertIsNone(await self._zeile("s-eigen"))

    async def test_fremden_zeitplan_ohne_agent_weder_loeschen_noch_ausloesen(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fehler:
                await api.delete_schedule("s-fremd", user=MITGLIED, db=db)
        self.assertEqual(fehler.exception.status_code, 403)
        async with self.Session() as db:
            with self.assertRaises(HTTPException):
                await api.trigger_schedule("s-fremd", user=MITGLIED, db=db)
        self.assertIsNotNone(await self._zeile("s-fremd"))


class KandidatenBeimFeuern(_Basis):
    """Wer darf einen Zeitplan ohne Agent ausführen? Die Agenten seines Urhebers."""

    async def test_mitglied_nur_eigene(self):
        async with self.Session() as db:
            kandidaten = await api.kandidaten_fuer_urheber(db, "ua")
        self.assertIn("a1", kandidaten)
        self.assertNotIn("b1", kandidaten)

    async def test_admin_alle(self):
        async with self.Session() as db:
            self.assertIsNone(await api.kandidaten_fuer_urheber(db, "adm"))

    async def test_unbekannter_oder_fehlender_urheber(self):
        async with self.Session() as db:
            with self.assertRaises(LookupError):
                await api.kandidaten_fuer_urheber(db, None)
            with self.assertRaises(LookupError):
                await api.kandidaten_fuer_urheber(db, "geloescht")


class SchedulerOhneAgent(unittest.IsolatedAsyncioTestCase):
    """Beim Feuern: Zeitplan ohne Agent darf nur unter den Agenten des Urhebers
    landen; ohne bekannten Urheber (Altbestand) wird er angehalten statt
    irgendeinem Agenten der Anlage zugeteilt."""

    def _zeitplan(self, created_by):
        return Schedule(
            id="s1", name="Bericht", prompt="p", interval_seconds=3600, cron_expression=None,
            timezone="UTC", agent_id=None, created_by=created_by, enabled=True,
            next_run_at=datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc),
            total_runs=0, success_count=0, fail_count=0,
        )

    async def _feuern(self, zeitplan, kandidaten):
        from app.services.scheduler_service import SchedulerService

        svc = SchedulerService(redis=MagicMock())
        svc._clear_retry_budgets = AsyncMock()
        router = MagicMock(create_and_route_task=AsyncMock(return_value=SimpleNamespace(id="t1")))
        db = MagicMock(add=MagicMock(), execute=AsyncMock())
        with patch.object(api, "kandidaten_fuer_urheber", kandidaten):
            await svc._execute_schedule(db, router, zeitplan, datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc))
        return router, db

    async def test_mitglied_zeitplan_laeuft_nur_auf_eigenen_agenten(self):
        router, _ = await self._feuern(self._zeitplan("ua"), AsyncMock(return_value={"a1"}))
        router.create_and_route_task.assert_awaited_once()
        self.assertEqual(router.create_and_route_task.await_args.kwargs["erlaubte_agenten"], {"a1"})

    async def test_altbestand_ohne_urheber_wird_angehalten(self):
        zeitplan = self._zeitplan(None)
        router, db = await self._feuern(zeitplan, AsyncMock(side_effect=LookupError("unbekannt")))
        router.create_and_route_task.assert_not_awaited()
        self.assertFalse(zeitplan.enabled)
        db.add.assert_called_once()          # Hinweis an die Administration


if __name__ == "__main__":
    unittest.main()
