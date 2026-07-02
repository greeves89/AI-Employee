"""Tests for job-state persistence + auto-resume (issue #211, Teil 2)."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.models.job_state import JobState
from app.services.job_state import (
    _RESUME_STALE_AFTER,
    classify_on_startup,
    is_resumable,
    recover_jobs_on_startup,
)


def _job(status="running", heartbeat_age=timedelta(minutes=1), **kw):
    now = datetime.now(timezone.utc)
    return JobState(
        id=kw.get("id", "job-1"),
        kind=kw.get("kind", "skill_import"),
        ref_id=kw.get("ref_id"),
        step=kw.get("step", "processing"),
        progress_pct=kw.get("progress_pct", 42.0),
        status=status,
        last_heartbeat=now - heartbeat_age,
        resume_count=kw.get("resume_count", 0),
        job_metadata=kw.get("job_metadata", {}),
    )


def _mock_db(rows):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


class TestResumeDecision(unittest.TestCase):
    def test_fresh_running_job_is_resumable(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(is_resumable(_job(heartbeat_age=timedelta(minutes=2)), now))

    def test_stale_running_job_not_resumable(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(
            is_resumable(_job(heartbeat_age=_RESUME_STALE_AFTER + timedelta(minutes=1)), now)
        )

    def test_completed_job_never_resumable(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(is_resumable(_job(status="completed"), now))

    def test_naive_heartbeat_is_normalized(self):
        now = datetime.now(timezone.utc)
        job = _job()
        job.last_heartbeat = (now - timedelta(minutes=1)).replace(tzinfo=None)
        self.assertTrue(is_resumable(job, now))

    def test_classify_splits_resumable_and_crashed(self):
        now = datetime.now(timezone.utc)
        fresh = _job(id="fresh", heartbeat_age=timedelta(minutes=1))
        stale = _job(id="stale", heartbeat_age=timedelta(hours=2))
        done = _job(id="done", status="completed")
        resumable, crashed = classify_on_startup([fresh, stale, done], now)
        self.assertEqual([j.id for j in resumable], ["fresh"])
        self.assertEqual([j.id for j in crashed], ["stale"])


class TestRecoverOnStartup(unittest.IsolatedAsyncioTestCase):
    async def test_long_job_resumes_after_container_restart(self):
        """Acceptance: a long job checkpointing before a restart is resumed, not lost."""
        now = datetime.now(timezone.utc)
        # A long-running job that checkpointed 3 minutes ago, then the container died.
        long_job = _job(
            id="import-9000",
            kind="skill_import",
            step="importing skill 900/2000",
            progress_pct=45.0,
            heartbeat_age=timedelta(minutes=3),
        )
        db = _mock_db([long_job])

        resumable, crashed = await recover_jobs_on_startup(db, now=now)

        # It survives the restart: classified resumable, resume_count bumped, still running.
        self.assertEqual([j.id for j in resumable], ["import-9000"])
        self.assertEqual(crashed, [])
        self.assertEqual(long_job.resume_count, 1)
        self.assertEqual(long_job.status, "running")
        # Progress is preserved so the job can pick up where it left off.
        self.assertEqual(long_job.progress_pct, 45.0)
        self.assertEqual(long_job.step, "importing skill 900/2000")
        db.commit.assert_awaited()

    async def test_stale_job_marked_crashed(self):
        now = datetime.now(timezone.utc)
        stale = _job(id="dead-1", heartbeat_age=timedelta(hours=3))
        db = _mock_db([stale])

        resumable, crashed = await recover_jobs_on_startup(db, now=now)

        self.assertEqual(resumable, [])
        self.assertEqual([j.id for j in crashed], ["dead-1"])
        self.assertEqual(stale.status, "crashed")
        self.assertIn("crashed_at", stale.job_metadata)
        self.assertIsNotNone(stale.error)
        db.commit.assert_awaited()

    async def test_mixed_batch_resumes_fresh_and_crashes_stale(self):
        now = datetime.now(timezone.utc)
        fresh = _job(id="fresh", heartbeat_age=timedelta(minutes=2))
        stale = _job(id="stale", heartbeat_age=timedelta(hours=1))
        db = _mock_db([fresh, stale])

        resumable, crashed = await recover_jobs_on_startup(db, now=now)

        self.assertEqual([j.id for j in resumable], ["fresh"])
        self.assertEqual([j.id for j in crashed], ["stale"])
        self.assertEqual(fresh.resume_count, 1)
        self.assertEqual(stale.status, "crashed")


if __name__ == "__main__":
    unittest.main()
