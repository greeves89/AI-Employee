"""Resume-decision logic for long-running jobs (issue #211, Teil 2).

Pure, dependency-light functions plus thin DB helpers. Kept free of docker/redis
imports so the resume logic can be unit-tested without the full app import chain.

Lifecycle:
  running   — job is actively checkpointing its heartbeat
  completed — job finished normally (no resume needed)
  crashed   — heartbeat went stale across a restart; not auto-resumed, user alerted

On startup `classify_on_startup` inspects every `running` row: rows with a fresh
heartbeat are resumable (the container restarted but the job's last checkpoint is
recent), rows whose heartbeat is older than the stale threshold are marked crashed.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.job_state import JobState

# A running job whose last heartbeat is within this window is considered
# resumable after a restart. Beyond it we assume the job died mid-flight.
_RESUME_STALE_AFTER = timedelta(minutes=15)


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalize a naive datetime to UTC-aware (DB may return tz-naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_resumable(job: JobState, now: datetime, stale_after: timedelta = _RESUME_STALE_AFTER) -> bool:
    """A job can be auto-resumed when it was running with a recent heartbeat."""
    if job.status != "running":
        return False
    hb = as_utc(job.last_heartbeat)
    if hb is None:
        return False
    return (now - hb) <= stale_after


def classify_on_startup(
    jobs: list[JobState], now: datetime, stale_after: timedelta = _RESUME_STALE_AFTER
) -> tuple[list[JobState], list[JobState]]:
    """Split running jobs into (resumable, crashed) based on heartbeat freshness."""
    resumable: list[JobState] = []
    crashed: list[JobState] = []
    for job in jobs:
        if job.status != "running":
            continue
        if is_resumable(job, now, stale_after):
            resumable.append(job)
        else:
            crashed.append(job)
    return resumable, crashed


async def recover_jobs_on_startup(db, now: datetime | None = None) -> tuple[list[JobState], list[JobState]]:
    """Load running jobs, mark stale ones crashed, and commit.

    Returns (resumable, crashed). Callers re-enqueue the resumable jobs and alert
    on the crashed ones. Marking crashed here (rather than in the caller) keeps the
    DB consistent even if the caller aborts before scheduling resumes.
    """
    now = now or datetime.now(timezone.utc)
    result = await db.execute(select(JobState).where(JobState.status == "running"))
    running = list(result.scalars().all())
    resumable, crashed = classify_on_startup(running, now)

    for job in resumable:
        job.resume_count = (job.resume_count or 0) + 1

    for job in crashed:
        job.status = "crashed"
        job.error = "Container restart: no heartbeat within resume window — job did not survive."
        meta = dict(job.job_metadata or {})
        meta["crashed_at"] = now.isoformat()
        job.job_metadata = meta

    await db.commit()
    return resumable, crashed


async def checkpoint(
    db,
    job_id: str,
    *,
    kind: str | None = None,
    ref_id: str | None = None,
    step: str | None = None,
    progress_pct: float | None = None,
    status: str | None = None,
    metadata: dict | None = None,
    now: datetime | None = None,
) -> JobState:
    """Upsert a job checkpoint and bump the heartbeat.

    A long-running job calls this at each step boundary. The heartbeat is always
    refreshed so the startup classifier can tell a live job from a dead one.
    """
    now = now or datetime.now(timezone.utc)
    result = await db.execute(select(JobState).where(JobState.id == job_id))
    job = result.scalars().first()

    if job is None:
        job = JobState(
            id=job_id,
            kind=kind or "job",
            ref_id=ref_id,
            step=step or "",
            progress_pct=progress_pct or 0.0,
            status=status or "running",
            last_heartbeat=now,
            job_metadata=metadata or {},
        )
        db.add(job)
    else:
        if kind is not None:
            job.kind = kind
        if ref_id is not None:
            job.ref_id = ref_id
        if step is not None:
            job.step = step
        if progress_pct is not None:
            job.progress_pct = progress_pct
        if status is not None:
            job.status = status
        if metadata is not None:
            job.job_metadata = {**(job.job_metadata or {}), **metadata}
        job.last_heartbeat = now

    await db.commit()
    return job
