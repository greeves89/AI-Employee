"""Kontingent-Bremse (core/kontingent): erschöpftes Abo → Zeitpläne pausieren bis zur Freigabe.

Vorher scheiterte bis zur Freigabe JEDER Zeitplan-Lauf sofort (am 27.–30.08.2026: 23
Läufe „You've hit your limit“), und ein gestoppter Agent wurde dafür jedes Mal geweckt.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import kontingent
from app.models.agent import Agent, AgentState
from app.models.schedule import Schedule
from app.models.task import TaskStatus

# 10:00 UTC = 11:00 in Berlin (MEZ)
JETZT = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


class BisWannTests(unittest.TestCase):

    def test_claude_resets_uhrzeit_mit_zone(self):
        self.assertEqual(kontingent.bis_wann("You've hit your limit · resets 1pm (Europe/Berlin)", JETZT),
                         datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))

    def test_uhrzeit_schon_vorbei_heisst_morgen(self):
        self.assertEqual(kontingent.bis_wann("You've hit your limit - resets 9am (Europe/Berlin)", JETZT),
                         datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc))

    def test_minuten_und_ohne_zone_utc(self):
        self.assertEqual(kontingent.bis_wann("You've hit your usage limit · resets 3:30pm", JETZT),
                         datetime(2026, 11, 7, 15, 30, tzinfo=timezone.utc))

    def test_wochenlimit_mit_datum(self):
        self.assertEqual(
            kontingent.bis_wann("You've hit your weekly limit · resets Nov 10, 9am (Europe/Berlin)", JETZT),
            datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc))

    def test_codex_try_again_in(self):
        self.assertEqual(kontingent.bis_wann(
            "You've hit your usage limit. Upgrade to Pro or try again in 2 hours 5 minutes.", JETZT),
            JETZT + timedelta(hours=2, minutes=5))
        self.assertEqual(kontingent.bis_wann("try again in 1 day 3 hours", JETZT),
                         JETZT + timedelta(days=1, hours=3))

    def test_unlesbar_eine_stunde(self):
        self.assertEqual(kontingent.bis_wann("429 Too Many Requests", JETZT), JETZT + timedelta(hours=1))
        self.assertEqual(kontingent.bis_wann(None, JETZT), JETZT + timedelta(hours=1))

    def test_grenzen(self):
        self.assertEqual(kontingent.bis_wann("try again in 30 days", JETZT), JETZT + timedelta(days=7))
        self.assertEqual(kontingent.bis_wann("try again in 1 minute", JETZT), JETZT + timedelta(minutes=5))

    def test_unbekannte_zone_gilt_als_utc(self):
        self.assertEqual(kontingent.bis_wann("resets 1pm (Mars/Olympus)", JETZT),
                         datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc))


class _Client:
    def __init__(self):
        self.werte: dict[str, str] = {}
        self.ablauf: dict[str, int] = {}

    async def set(self, key, value, exat=None, **_):
        self.werte[key] = value
        self.ablauf[key] = exat
        return True

    async def get(self, key):
        return self.werte.get(key)

    async def delete(self, *keys):
        for k in keys:
            self.werte.pop(k, None)
        return len(keys)


class _Redis:
    def __init__(self):
        self.client = _Client()

    async def get_queue_depth(self, _agent_id):
        return 0


class SpeicherTests(unittest.IsolatedAsyncioTestCase):

    async def test_neu_verlaengern_nie_verkuerzen_aufheben(self):
        r = _Redis()
        jetzt = datetime.now(timezone.utc)
        bis = jetzt + timedelta(hours=2)
        self.assertTrue(await kontingent.pausieren(r, "a1", bis), "neu → einmal melden")
        self.assertEqual(await kontingent.pausiert_bis(r, "a1"), bis)
        self.assertEqual(r.client.ablauf["kontingent:pause:a1"], int(bis.timestamp()), "Redis räumt selbst auf")
        self.assertFalse(await kontingent.pausieren(r, "a1", bis - timedelta(hours=1)), "nie verkürzen")
        self.assertEqual(await kontingent.pausiert_bis(r, "a1"), bis)
        self.assertFalse(await kontingent.pausieren(r, "a1", bis + timedelta(hours=1)), "verlängert, nicht neu")
        self.assertEqual(await kontingent.pausiert_bis(r, "a1"), bis + timedelta(hours=1))
        await kontingent.aufheben(r, "a1")
        self.assertIsNone(await kontingent.pausiert_bis(r, "a1"))

    async def test_ohne_redis_keine_bremse(self):
        r = SimpleNamespace(client=None)
        self.assertFalse(await kontingent.pausieren(r, "a1", datetime.now(timezone.utc) + timedelta(hours=1)))
        self.assertIsNone(await kontingent.pausiert_bis(r, "a1"))

    async def test_kaputter_eintrag_bremst_nicht(self):
        r = _Redis()
        r.client.werte["kontingent:pause:a1"] = "kein json"
        self.assertIsNone(await kontingent.pausiert_bis(r, "a1"))


class RouterTests(unittest.IsolatedAsyncioTestCase):
    """``TaskRouter._kontingent_vermerken`` — an der Stelle, an der ein Lauf abgeschlossen wird."""

    def _router(self):
        from app.core.task_router import TaskRouter
        router = TaskRouter.__new__(TaskRouter)
        router.redis = _Redis()
        router.db = SimpleNamespace(add=lambda n: setattr(self, "meldung", n), commit=AsyncMock(),
                                    refresh=AsyncMock())
        router._publish_notification = AsyncMock()
        self.meldung = None
        return router

    def _lauf(self, status, error=None, result=None):
        return SimpleNamespace(id="t1", status=status, error=error, result=result)

    async def test_kontingent_lauf_pausiert_und_meldet_einmal(self):
        router = self._router()
        lauf = self._lauf(TaskStatus.FAILED, error=kontingent.GRUND,
                          result="You've hit your limit · resets 1pm (Europe/Berlin)")
        await router._kontingent_vermerken("a1", lauf)
        self.assertIsNotNone(await kontingent.pausiert_bis(router.redis, "a1"))
        self.assertIsNotNone(self.meldung)
        self.assertEqual(self.meldung.meta["type"], "kontingent_pause")
        self.assertIn("pausieren bis", self.meldung.message)
        self.meldung = None
        await router._kontingent_vermerken("a1", lauf)
        self.assertIsNone(self.meldung, "zweiter Lauf: keine zweite Meldung")

    async def test_gescheiterter_lauf_mit_limit_im_fehlertext(self):
        """Codex meldet das Limit als Fehler, nicht als „fertig“."""
        router = self._router()
        await router._kontingent_vermerken("a1", self._lauf(
            TaskStatus.FAILED, error="You've hit your usage limit. Try again in 2 hours."))
        bis = await kontingent.pausiert_bis(router.redis, "a1")
        self.assertIsNotNone(bis)
        self.assertAlmostEqual((bis - datetime.now(timezone.utc)).total_seconds(), 7200, delta=60)

    async def test_anderer_fehler_bremst_nicht(self):
        router = self._router()
        await router._kontingent_vermerken("a1", self._lauf(TaskStatus.FAILED, error="Connection closed by server"))
        self.assertIsNone(await kontingent.pausiert_bis(router.redis, "a1"))
        self.assertIsNone(self.meldung)

    async def test_erfolg_hebt_die_pause_auf(self):
        router = self._router()
        await kontingent.pausieren(router.redis, "a1", datetime.now(timezone.utc) + timedelta(hours=1))
        await router._kontingent_vermerken("a1", self._lauf(TaskStatus.COMPLETED, result="Fertig. Beleg: ok"))
        self.assertIsNone(await kontingent.pausiert_bis(router.redis, "a1"))

    async def test_stoerung_stoppt_das_abschliessen_nie(self):
        router = self._router()
        with patch.object(kontingent, "pausieren", AsyncMock(side_effect=RuntimeError("redis weg"))):
            await router._kontingent_vermerken("a1", self._lauf(TaskStatus.FAILED, error=kontingent.GRUND))


class _Ergebnis:
    def __init__(self, wert):
        self.wert = wert

    def scalar_one_or_none(self):
        return self.wert


class SchedulerTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from app.services.scheduler_service import SchedulerService
        self.now = datetime.now(timezone.utc)
        self.agent = Agent(id="a1", name="Agent", state=AgentState.STOPPED, config={})
        self.redis = _Redis()
        self.svc = SchedulerService(redis=self.redis)
        self.svc._stale_task_count = AsyncMock(return_value=0)
        self.schedule = Schedule(id="s1", name="[Rhythmus] Morgencheck", prompt="x", cron_expression="0 * * * *",
                                 timezone="UTC", interval_seconds=3600, agent_id="a1", next_run_at=self.now,
                                 enabled=True, total_runs=0, success_count=0, fail_count=0)

    async def _ausfuehren(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=_Ergebnis(self.agent)))
        await self.svc._execute_schedule(db, SimpleNamespace(), self.schedule, self.now)

    async def test_pause_verschiebt_auf_die_freigabe_und_weckt_nicht(self):
        bis = self.now + timedelta(hours=2)
        await kontingent.pausieren(self.redis, "a1", bis)
        with patch("app.core.agent_wakeup.ensure_agent_running", AsyncMock()) as wecken:
            await self._ausfuehren()
        wecken.assert_not_awaited()
        self.assertEqual(self.schedule.next_run_at, bis + timedelta(minutes=1))
        self.assertEqual(self.schedule.total_runs, 0)

    async def test_ohne_pause_geht_es_weiter_wie_bisher(self):
        """Ohne Pause greift die Bremse nicht ein — der Lauf geht in die bisherige Prüfung."""
        with patch.object(self.svc, "_ueberspringen_vor_dem_wecken", AsyncMock(return_value=True)) as weiter:
            await self._ausfuehren()
        weiter.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
