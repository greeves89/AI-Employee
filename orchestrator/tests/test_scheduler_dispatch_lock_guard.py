"""Guard test: dispatch to ANY schedule type must go through the atomic
per-agent dispatch lock (#548) — not just [Proactive] schedules, and not as a
non-atomic read-then-push.

Root cause (see issue #548 root-cause comment, 2026-08-10): the busy-check that
already existed only ever ran for schedules whose name starts with
"[Proactive]", built from two separate Redis reads with no lock in between, and
regular/[Plan] schedules had no busy-check at all. Two schedules due for the
same agent in the same tick (or overlapping ticks) could both dispatch,
producing two concurrent `claude -p` processes racing on the same shared
/workspace checkout — confirmed 3rd+ occurrence.

Source-level guard (same style as test_scheduler_skips_stopped_agents.py) so it
runs without pulling in fastapi/sqlalchemy/croniter in the agent container.
"""

import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app/services/scheduler_service.py").read_text()
FIRE = SRC.split("Create a task from a schedule and advance next_run_at", 1)[1].split(
    "async def _proactive_config", 1
)[0]

# The final, unconditional dispatch call — must be guarded, not free-standing.
_DISPATCH_CALL = "router.create_and_route_task("


class DispatchLockGuardTests(unittest.TestCase):
    def test_lock_is_acquired_before_the_final_busy_check(self):
        self.assertIn("acquire_dispatch_lock(schedule.agent_id)", FIRE)
        # "is_busy_with_task" also appears in the earlier [Proactive]-only
        # fast-path check — we need the occurrence tied to OUR lock, i.e. the
        # last one, which sits right after the acquire call.
        self.assertLess(
            FIRE.index("acquire_dispatch_lock("), FIRE.rindex("is_busy_with_task"),
            "the lock must be held BEFORE re-reading queue_depth/status, "
            "otherwise the read-then-push window is still racy",
        )

    def test_final_check_is_not_restricted_to_proactive_schedules(self):
        """The [Proactive]-only early check is fine to keep as a fast-path
        filter, but the FINAL check right before dispatch (the one under the
        lock) must apply to every schedule type — that's what closes the gap
        for [Plan] blocks, which previously had no guard at all."""
        # Locate the final lock-guarded block specifically (the one that wraps
        # the dispatch call), not the earlier [Proactive]-only fast path.
        pre_dispatch = FIRE.split(_DISPATCH_CALL, 1)[0]
        guard_section = pre_dispatch.rsplit("acquire_dispatch_lock(schedule.agent_id)", 1)[0]
        # The guard must be gated on schedule.agent_id alone, not on the
        # schedule name — i.e. no "startswith(\"[Proactive]\")" between the
        # lock acquisition and the dispatch call.
        lock_to_dispatch = pre_dispatch[len(guard_section):]
        self.assertNotIn('schedule.name.startswith("[Proactive]")', lock_to_dispatch)

    def test_dispatch_call_is_inside_a_try_finally_that_releases_the_lock(self):
        segment = FIRE.split(_DISPATCH_CALL, 1)
        self.assertEqual(len(segment), 2, "expected exactly one dispatch call site")
        before, after = segment
        self.assertIn("try:", before[-200:])
        self.assertIn("finally:", after[:400])
        self.assertIn("release_dispatch_lock(schedule.agent_id, lock_token)", after[:600])

    def test_busy_dispatcher_releases_the_lock_before_returning(self):
        """If the atomic re-check finds the agent busy, the lock must be
        released explicitly (the schedule returns immediately, never reaching
        the try/finally around the dispatch call)."""
        pre_dispatch = FIRE.split(_DISPATCH_CALL, 1)[0]
        busy_branch = pre_dispatch.split("is_busy_with_task:", 1)[1]
        self.assertIn("release_dispatch_lock(schedule.agent_id, lock_token)", busy_branch)

    def test_lock_held_dispatcher_retries_next_tick_without_reading_status(self):
        """When acquire_dispatch_lock returns None (another dispatch for this
        agent is already in flight), we must NOT proceed to read queue depth /
        agent status at all — just back off and let the next tick decide."""
        pre_dispatch = FIRE.split(_DISPATCH_CALL, 1)[0]
        lock_rejected_branch = pre_dispatch.split("lock_token is None:", 1)[1].split(
            "queue_depth = await self.redis.get_queue_depth", 1
        )[0]
        self.assertIn("schedule.next_run_at = _calc_next_run(schedule, now)", lock_rejected_branch)
        self.assertIn("return", lock_rejected_branch)


if __name__ == "__main__":
    unittest.main()
