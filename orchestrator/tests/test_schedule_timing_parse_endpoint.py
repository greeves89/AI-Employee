"""Issue #196: the ``POST /schedules/parse-timing`` preview endpoint.

Creates nothing — resolves a free-text timing phrase to a cron_expression
(+ timezone + a short human-readable preview) that the caller then passes
into the existing, unchanged ``POST /schedules/`` the normal way. There is
still only one code path that actually creates a schedule.
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.api.schedules import parse_schedule_timing
from app.models.agent import Agent
from app.schemas.schedule import ScheduleTimingParseRequest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _FakeDbNoAgent:
    """Stand-in for a session that is never actually queried (no agent_id given)."""
    async def execute(self, *a, **kw):
        raise AssertionError("db.execute must not be called when no agent_id is given")


class ParseScheduleTimingWithoutAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic_phrase_resolves_without_touching_the_db(self):
        body = ScheduleTimingParseRequest(text="jeden Montag um 9 Uhr")
        result = await parse_schedule_timing(body, user=MagicMock(), db=_FakeDbNoAgent())
        self.assertEqual(result.cron_expression, "0 9 * * 1")
        self.assertEqual(result.timezone, "UTC")
        self.assertEqual(result.source, "regel")
        self.assertIn("Montag", result.explanation)

    async def test_next_runs_are_populated_and_in_the_future(self):
        body = ScheduleTimingParseRequest(text="täglich um 8 Uhr")
        result = await parse_schedule_timing(body, user=MagicMock(), db=_FakeDbNoAgent())
        self.assertEqual(len(result.next_runs), 3)
        now = datetime.now(timezone.utc)
        for run in result.next_runs:
            self.assertGreater(run, now)

    async def test_explicit_timezone_overrides_the_utc_default(self):
        body = ScheduleTimingParseRequest(text="täglich um 8 Uhr", timezone="Europe/Berlin")
        result = await parse_schedule_timing(body, user=MagicMock(), db=_FakeDbNoAgent())
        self.assertEqual(result.timezone, "Europe/Berlin")

    async def test_unresolvable_phrase_is_a_422(self):
        body = ScheduleTimingParseRequest(text="wenn die Sonne scheint")
        with self.assertRaises(HTTPException) as ctx:
            await parse_schedule_timing(body, user=MagicMock(), db=_FakeDbNoAgent())
        self.assertEqual(ctx.exception.status_code, 422)


class ParseScheduleTimingWithAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(
                id="a1", name="Testagent",
                config={"proactive": {"contact_hours": {"timezone": "Europe/Berlin"}}},
            ))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_timezone_resolves_from_the_agents_own_config(self):
        async with self.Session() as db:
            body = ScheduleTimingParseRequest(text="täglich um 8 Uhr", agent_id="a1")
            result = await parse_schedule_timing(body, user=MagicMock(), db=db)
        self.assertEqual(result.timezone, "Europe/Berlin")

    async def test_an_explicit_timezone_still_wins_over_the_agents_own(self):
        async with self.Session() as db:
            body = ScheduleTimingParseRequest(text="täglich um 8 Uhr", agent_id="a1", timezone="UTC")
            result = await parse_schedule_timing(body, user=MagicMock(), db=db)
        self.assertEqual(result.timezone, "UTC")


if __name__ == "__main__":
    unittest.main()
