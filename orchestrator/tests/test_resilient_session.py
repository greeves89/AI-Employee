"""Tests for ``resilient_session`` — the connect-retry wrapper for background
sweeps (issue #356).

A brief DB blip used to make ``asyncpg.connect`` time out, bubble up, and kill an
entire background sweep tick (50+ ERRORs from a single ~1h outage). The helper
retries only the *connection establishment* (via a ``SELECT 1`` pre-ping) with
exponential backoff + jitter, so a few seconds of unavailability are bridged
instead of skipping the whole tick. Errors raised inside the body must NOT be
retried.
"""

import unittest

from app.db import session as db_session
from app.db.session import resilient_session


class _FakeSession:
    """Doubles as the async-context "connection" and the session it yields —
    mirrors how a real AsyncSession returned by ``async_session_factory()`` both
    enters a context and executes queries."""

    def __init__(self, fail_ping: bool) -> None:
        self._fail_ping = fail_ping
        self._pinged = False
        self.closed = False
        self.body_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def execute(self, _stmt):
        if not self._pinged:
            self._pinged = True
            if self._fail_ping:
                raise TimeoutError("simulated connect timeout")
            return "ping-ok"
        self.body_calls += 1
        return "body-ok"


def _factory(fail_first_n: int):
    state = {"attempts": 0}
    created: list[_FakeSession] = []

    def factory() -> _FakeSession:
        state["attempts"] += 1
        sess = _FakeSession(fail_ping=state["attempts"] <= fail_first_n)
        created.append(sess)
        return sess

    return factory, created


class TestResilientSession(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_succeeds(self):
        factory, created = _factory(fail_first_n=2)
        async with resilient_session(
            retries=3, base_delay=0.001, session_factory=factory
        ) as db:
            result = await db.execute("do work")
        self.assertEqual(result, "body-ok")
        # 2 failed connects + 1 successful = 3 sessions created.
        self.assertEqual(len(created), 3)
        # The two failed sessions were closed; the live one was closed on exit.
        self.assertTrue(all(s.closed for s in created))
        self.assertIs(db, created[-1])

    async def test_gives_up_after_retries_and_closes(self):
        factory, created = _factory(fail_first_n=99)
        with self.assertRaises(TimeoutError):
            async with resilient_session(
                retries=3, base_delay=0.001, session_factory=factory
            ):
                self.fail("body must not run when connect never succeeds")
        # 1 initial attempt + 3 retries = 4 sessions, all closed.
        self.assertEqual(len(created), 4)
        self.assertTrue(all(s.closed for s in created))

    async def test_body_errors_are_not_retried(self):
        factory, created = _factory(fail_first_n=0)

        class _BodyError(Exception):
            pass

        with self.assertRaises(_BodyError):
            async with resilient_session(
                retries=3, base_delay=0.001, session_factory=factory
            ):
                raise _BodyError()
        # Connect succeeded once → exactly one session, and it was closed.
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    async def test_defaults_to_module_factory(self):
        # When no session_factory is passed, the module-level factory is used.
        self.assertIsNotNone(db_session.async_session_factory)


if __name__ == "__main__":
    unittest.main()
