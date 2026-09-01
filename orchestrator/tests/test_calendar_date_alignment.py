"""`ms_list_calendar_events` mit `date` — Gegenstueck zum rollierenden `days_ahead`.

Live gemeldet (27.08.2026): "Termine fuer morgen" mit days_ahead=1 lieferte
ueberwiegend den REST VON HEUTE, weil days_ahead ein Fenster ab dem exakten
JETZT ist, kein Kalendertag. `date='tomorrow'` muss echte Mitternacht-zu-
Mitternacht-Grenzen in der angegebenen Zeitzone liefern.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.core import msgraph_mcp


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, token, **kwargs):
        self.calls.append({"method": method, "path": path, "kwargs": kwargs})
        return {"value": []}


class DateAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_today_is_midnight_to_midnight_not_now_onward(self):
        rec = _Recorder()
        with patch.object(msgraph_mcp, "_graph", new=rec):
            await msgraph_mcp.handle_tool(
                "ms_list_calendar_events", {"date": "today", "timezone": "Europe/Berlin"}, "tok",
            )
        params = rec.calls[0]["kwargs"]["params"]
        start = datetime.fromisoformat(params["startDateTime"])
        end = datetime.fromisoformat(params["endDateTime"])
        self.assertEqual((end - start).total_seconds(), 86400)
        # Grenze faellt auf lokale Mitternacht (UTC-Offset macht die Minute/Sekunde exakt 0).
        self.assertEqual(start.astimezone(timezone.utc).minute, 0)
        self.assertEqual(start.astimezone(timezone.utc).second, 0)

    async def test_tomorrow_is_exactly_one_day_after_today(self):
        rec = _Recorder()
        with patch.object(msgraph_mcp, "_graph", new=rec):
            await msgraph_mcp.handle_tool(
                "ms_list_calendar_events", {"date": "today", "timezone": "Europe/Berlin"}, "tok",
            )
            today_start = datetime.fromisoformat(rec.calls[0]["kwargs"]["params"]["startDateTime"])
            await msgraph_mcp.handle_tool(
                "ms_list_calendar_events", {"date": "tomorrow", "timezone": "Europe/Berlin"}, "tok",
            )
            tomorrow_start = datetime.fromisoformat(rec.calls[1]["kwargs"]["params"]["startDateTime"])
        self.assertEqual((tomorrow_start - today_start).total_seconds(), 86400)

    async def test_an_explicit_iso_date_is_honoured(self):
        rec = _Recorder()
        with patch.object(msgraph_mcp, "_graph", new=rec):
            await msgraph_mcp.handle_tool(
                "ms_list_calendar_events", {"date": "2026-12-24", "timezone": "Europe/Berlin"}, "tok",
            )
        start = datetime.fromisoformat(rec.calls[0]["kwargs"]["params"]["startDateTime"])
        # In der ANGEFRAGTEN Zone pruefen, nicht in der des Rechners: bare
        # .astimezone() nimmt die lokale Zone und ist damit auf einem UTC-Runner rot.
        self.assertEqual(
            start.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat(), "2026-12-24"
        )

    async def test_no_date_falls_back_to_the_old_rolling_window_unchanged(self):
        """days_ahead bleibt fuer Mehrtagesuebersichten nuetzlich — Bestandsverhalten."""
        rec = _Recorder()
        with patch.object(msgraph_mcp, "_graph", new=rec):
            await msgraph_mcp.handle_tool("ms_list_calendar_events", {"days_ahead": 7}, "tok")
        params = rec.calls[0]["kwargs"]["params"]
        start = datetime.fromisoformat(params["startDateTime"])
        end = datetime.fromisoformat(params["endDateTime"])
        self.assertAlmostEqual((end - start).total_seconds(), 7 * 86400, delta=5)

    async def test_date_takes_priority_over_days_ahead_if_both_given(self):
        rec = _Recorder()
        with patch.object(msgraph_mcp, "_graph", new=rec):
            await msgraph_mcp.handle_tool(
                "ms_list_calendar_events", {"date": "today", "days_ahead": 30}, "tok",
            )
        params = rec.calls[0]["kwargs"]["params"]
        start = datetime.fromisoformat(params["startDateTime"])
        end = datetime.fromisoformat(params["endDateTime"])
        self.assertEqual((end - start).total_seconds(), 86400)

    async def test_an_unknown_timezone_falls_back_instead_of_crashing(self):
        rec = _Recorder()
        with patch.object(msgraph_mcp, "_graph", new=rec):
            await msgraph_mcp.handle_tool(
                "ms_list_calendar_events", {"date": "today", "timezone": "Not/AZone"}, "tok",
            )
        self.assertEqual(len(rec.calls), 1)  # kein Absturz, ein Aufruf ist rausgegangen


if __name__ == "__main__":
    unittest.main()
