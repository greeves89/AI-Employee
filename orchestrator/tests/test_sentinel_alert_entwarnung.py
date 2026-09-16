"""Der Sentinel-Alarm bekommt Entwarnung und Ursachen-Kontext (#746 Punkt 3).

Am 15.09.2026 stand der Orchestrator 13 Minuten ohne Namensaufloesung. Redis
war damit weg, der Sentinel konnte sein Lebenszeichen nicht schreiben, der
Wachhund meldete ``urgent`` „Agenten laufen unbeaufsichtigt". Beides war
formal richtig — und trotzdem irrefuehrend: der Alarm nannte die DNS-Fehler
daneben nicht, und als das Lebenszeichen 30 Sekunden spaeter wieder frisch
war, erfuhr der Leser das nie. Ein ``urgent`` ohne Entwarnung ist ein
Dauerzustand im Kopf des Betreibers.

Geprueft wird das Verhalten des Ticks mit einem Redis- und einem DB-Doppel,
nicht der Quelltext: der Alarm muss den Kontext tragen, die Entwarnung muss
kommen, die Luecke muss beziffert sein, und ohne vorherigen Alarm darf es
keine Entwarnung geben.
"""

import socket
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import infra_error_window as iew
from app.core.infra_error_window import InfraErrorWindow
from app.services.scheduler_service import SchedulerService

JETZT = 1_789_485_600.0          # 15.09.2026 15:20:00Z, Unix-Sekunden


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _record(msg: str, exc: BaseException | None = None):
    import logging
    exc_info = (type(exc), exc, None) if exc is not None else None
    return logging.LogRecord("asyncio", logging.ERROR, __file__, 1, msg, None, exc_info)


class SentinelAlertContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.svc = SchedulerService.__new__(SchedulerService)
        self.svc._sentinel_alerted = False
        self.svc._sentinel_stumm_seit = None
        self.redis = MagicMock()
        self.redis.client = AsyncMock()
        self.svc.redis = self.redis
        self.session = _FakeSession()
        self._alt_fenster = iew._fenster
        iew._fenster = InfraErrorWindow()

    def tearDown(self):
        iew._fenster = self._alt_fenster

    async def _tick(self, beat: str, now: float):
        """Ein Tick mit vorgegebenem Lebenszeichen und vorgegebener Uhr."""
        from datetime import datetime, timezone
        self.redis.client.get = AsyncMock(return_value=beat)
        fake_now = datetime.fromtimestamp(now, tz=timezone.utc)
        with patch("app.services.scheduler_service.resilient_session",
                   return_value=self.session), \
             patch("app.services.scheduler_service.datetime") as dt:
            dt.now.return_value = fake_now
            dt.fromtimestamp = datetime.fromtimestamp
            await self.svc._tick_sentinel_liveness()

    def _notifications(self, titel_teil: str):
        return [n for n in self.session.added if titel_teil in n.title]

    async def test_the_alert_names_the_dns_errors_that_ran_alongside(self):
        iew._fenster.emit(_record("Future exception was never retrieved",
                                  socket.gaierror(-3, "Temporary failure in name resolution")))
        iew._fenster.emit(_record("Failed to release lock: Timeout connecting to server"))
        await self._tick(str(JETZT - 300), JETZT)

        alarme = self._notifications("Sentinel antwortet nicht mehr")
        self.assertEqual(len(alarme), 1)
        self.assertEqual(alarme[0].priority, "urgent")
        self.assertIn("1x DNS-Aussetzer", alarme[0].message)
        self.assertIn("1x Redis-Verbindungsfehler", alarme[0].message)

    async def test_the_alert_comes_only_once_while_silent(self):
        """Alle 30 Sekunden dieselbe Meldung waere Laerm, kein Alarm."""
        await self._tick(str(JETZT - 300), JETZT)
        await self._tick(str(JETZT - 300), JETZT + 30)
        await self._tick(str(JETZT - 300), JETZT + 60)
        self.assertEqual(len(self._notifications("Sentinel antwortet nicht mehr")), 1)

    async def test_the_alert_says_what_it_means_for_the_operator(self):
        await self._tick(str(JETZT - 300), JETZT)
        self.assertIn("unbeaufsichtigt",
                      self._notifications("Sentinel antwortet nicht mehr")[0].message)

    async def test_the_alert_says_when_no_infra_errors_ran(self):
        await self._tick(str(JETZT - 300), JETZT)
        alarme = self._notifications("Sentinel antwortet nicht mehr")
        self.assertEqual(len(alarme), 1)
        self.assertIn("KEINE Redis-/DNS-/DB-Fehler", alarme[0].message)

    async def test_recovery_sends_an_all_clear_with_the_gap(self):
        """Stumm ab JETZT-300 s, um JETZT gemeldet, um JETZT+120 wieder frisch:
        die Luecke ist 7 Minuten (letztes altes Lebenszeichen bis neues)."""
        await self._tick(str(JETZT - 300), JETZT)
        self.assertTrue(self.svc._sentinel_alerted)

        await self._tick(str(JETZT + 120), JETZT + 125)

        entwarnungen = self._notifications("Sentinel meldet sich wieder")
        self.assertEqual(len(entwarnungen), 1)
        e = entwarnungen[0]
        self.assertEqual(e.type, "success")
        self.assertEqual(e.priority, "high",
                         "der Alarm ging per urgent nach Telegram — die Entwarnung "
                         "muss denselben Leser erreichen")
        self.assertIn("7 Minuten", e.message)
        self.assertFalse(self.svc._sentinel_alerted)
        self.assertEqual(self.session.commit.await_count, 2)

    async def test_no_all_clear_without_a_prior_alert(self):
        """Ein frisches Lebenszeichen im Normalbetrieb ist keine Nachricht wert."""
        await self._tick(str(JETZT - 10), JETZT)
        await self._tick(str(JETZT + 20), JETZT + 30)
        self.assertEqual(self.session.added, [])

    async def test_the_all_clear_comes_only_once(self):
        await self._tick(str(JETZT - 300), JETZT)
        await self._tick(str(JETZT + 120), JETZT + 125)
        await self._tick(str(JETZT + 150), JETZT + 155)
        self.assertEqual(len(self._notifications("Sentinel meldet sich wieder")), 1)

    async def test_a_second_outage_alerts_again(self):
        """Nach der Entwarnung ist der Wachhund wieder scharf."""
        await self._tick(str(JETZT - 300), JETZT)
        await self._tick(str(JETZT + 120), JETZT + 125)
        await self._tick(str(JETZT + 120), JETZT + 600)
        self.assertEqual(len(self._notifications("Sentinel antwortet nicht mehr")), 2)

    async def test_the_all_clear_is_retried_when_the_db_write_fails(self):
        """Faellt die Entwarnung der noch wackligen DB zum Opfer, bleibt der
        Alarm offen und der naechste Tick versucht es erneut — statt die
        Entwarnung still zu verlieren."""
        await self._tick(str(JETZT - 300), JETZT)

        kaputt = _FakeSession()
        kaputt.commit = AsyncMock(side_effect=TimeoutError("DB weg"))
        from datetime import datetime, timezone
        self.redis.client.get = AsyncMock(return_value=str(JETZT + 120))
        with patch("app.services.scheduler_service.resilient_session", return_value=kaputt), \
             patch("app.services.scheduler_service.datetime") as dt:
            dt.now.return_value = datetime.fromtimestamp(JETZT + 125, tz=timezone.utc)
            with self.assertRaises(TimeoutError):
                await self.svc._tick_sentinel_liveness()
        self.assertTrue(self.svc._sentinel_alerted)

        await self._tick(str(JETZT + 150), JETZT + 155)
        self.assertEqual(len(self._notifications("Sentinel meldet sich wieder")), 1)
        self.assertFalse(self.svc._sentinel_alerted)

    async def test_a_vanished_key_is_not_a_recovery(self):
        """Ist der Schluessel nach dem Alarm WEG (Redis-Neustart ohne
        Wiederherstellung, FLUSHALL), gibt es kein neues Lebenszeichen — also
        auch keine Entwarnung. Der Alarm bleibt stehen."""
        await self._tick(str(JETZT - 300), JETZT)
        await self._tick(None, JETZT + 30)
        self.assertEqual(self._notifications("Sentinel meldet sich wieder"), [])
        self.assertTrue(self.svc._sentinel_alerted)

    async def test_the_alert_line_does_not_poison_its_own_window(self):
        """Der Alarm zitiert den Hinweis („KEINE Redis-/DNS-/DB-Fehler") im
        ERROR-Log. Haengt das Fenster am Wurzel-Logger, darf diese Zeile
        nicht als Redis-Fehler zaehlen — sonst meldet der NAECHSTE Alarm
        „1x Redis-Verbindungsfehler", den es nie gab."""
        import logging
        from app.core.infra_error_window import setup_infra_error_window
        root = logging.getLogger()
        vorher = root.level
        h = setup_infra_error_window()
        try:
            await self._tick(str(JETZT - 300), JETZT)
            self.assertEqual(h.zusammenfassung(jetzt=JETZT + 1), {"dns": 0, "redis": 0, "db": 0})
        finally:
            root.removeHandler(h)
            root.setLevel(vorher)
            iew._fenster = None

    async def test_an_unreadable_old_beat_gives_an_all_clear_without_a_number(self):
        await self._tick("kaputt", JETZT)
        await self._tick(str(JETZT + 120), JETZT + 125)
        e = self._notifications("Sentinel meldet sich wieder")
        self.assertEqual(len(e), 1)
        self.assertIn("Dauer unbekannt", e[0].message)


class TheWindowIsInstalledTests(unittest.TestCase):
    """Ein Fehlerfenster, das niemand beim Start anbringt, misst nichts —
    und der Hinweis wuerde ehrlich, aber nutzlos „nicht gemessen" sagen."""

    def test_main_installs_the_window_next_to_the_error_log(self):
        """Geprueft wird der AUFRUF im Syntaxbaum, nicht der Text — ein
        auskommentierter Aufruf steht weiterhin im Quelltext und bestuende
        jedes assertIn (#726)."""
        import ast
        import pathlib
        tree = ast.parse((pathlib.Path(__file__).resolve().parents[1] / "app/main.py").read_text())
        aufrufe = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == "setup_infra_error_window"
        ]
        self.assertEqual(len(aufrufe), 1)
