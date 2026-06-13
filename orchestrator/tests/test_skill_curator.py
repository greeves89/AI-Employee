"""Tests for the Hermes-inspired SkillCurator service."""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.skill import Skill, SkillCategory, SkillStatus
from app.services.skill_curator import (
    SkillCurator,
    STALE_THRESHOLD_DAYS,
    ARCHIVE_THRESHOLD_DAYS,
)


_TABLES = [Base.metadata.tables["skills"]]


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_TABLES))

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _mk(session, *, name, status=SkillStatus.ACTIVE, last_used_days_ago=None,
        usage_count=0, avg_rating=None):
    now = datetime.now(timezone.utc)
    skill = Skill(
        name=name,
        description="t",
        content="t",
        category=SkillCategory.ROUTINE,
        status=status,
        usage_count=usage_count,
        avg_rating=avg_rating,
        last_used_at=now - timedelta(days=last_used_days_ago) if last_used_days_ago else None,
    )
    session.add(skill)
    return skill


@pytest.mark.asyncio
async def test_active_unused_long_enough_goes_stale(db: AsyncSession):
    _mk(db, name="a", status=SkillStatus.ACTIVE, last_used_days_ago=STALE_THRESHOLD_DAYS + 1)
    await db.commit()

    report = await SkillCurator(db).run()
    assert report.scanned == 1
    assert len(report.moved_to_stale) == 1
    assert len(report.moved_to_archived) == 0


@pytest.mark.asyncio
async def test_active_brand_new_stays_active(db: AsyncSession):
    _mk(db, name="b", status=SkillStatus.ACTIVE, last_used_days_ago=1)
    await db.commit()

    report = await SkillCurator(db).run()
    assert report.moved_to_stale == []


@pytest.mark.asyncio
async def test_stale_used_again_refreshes(db: AsyncSession):
    _mk(db, name="c", status=SkillStatus.STALE, last_used_days_ago=2)
    await db.commit()

    report = await SkillCurator(db).run()
    assert len(report.refreshed_to_active) == 1


@pytest.mark.asyncio
async def test_stale_unused_archives(db: AsyncSession):
    _mk(db, name="d", status=SkillStatus.STALE, last_used_days_ago=ARCHIVE_THRESHOLD_DAYS + 1)
    await db.commit()

    report = await SkillCurator(db).run()
    assert len(report.moved_to_archived) == 1


@pytest.mark.asyncio
async def test_low_rating_with_enough_usage_goes_stale(db: AsyncSession):
    _mk(db, name="e", status=SkillStatus.ACTIVE, last_used_days_ago=1,
        usage_count=5, avg_rating=1.5)
    await db.commit()

    report = await SkillCurator(db).run()
    assert len(report.moved_to_stale) == 1


@pytest.mark.asyncio
async def test_dry_run_does_not_persist(db: AsyncSession):
    skill = _mk(db, name="f", status=SkillStatus.ACTIVE,
                last_used_days_ago=STALE_THRESHOLD_DAYS + 1)
    await db.commit()

    report = await SkillCurator(db).run(dry_run=True)
    assert len(report.moved_to_stale) == 1
    await db.refresh(skill)
    assert skill.status == SkillStatus.ACTIVE


@pytest.mark.asyncio
async def test_probation_skill_is_protected(db: AsyncSession):
    """Skills mid-A/B-test must not be touched by the curator."""
    protected = _mk(db, name="prob", status=SkillStatus.ACTIVE,
                    last_used_days_ago=STALE_THRESHOLD_DAYS + 10,
                    usage_count=5, avg_rating=1.0)
    protected.improvement_status = "probation"
    await db.commit()

    report = await SkillCurator(db).run()
    assert report.scanned == 0  # excluded from the scan
    await db.refresh(protected)
    assert protected.status == SkillStatus.ACTIVE


@pytest.mark.asyncio
async def test_pending_review_skill_is_protected(db: AsyncSession):
    pending = _mk(db, name="pr", status=SkillStatus.ACTIVE,
                  last_used_days_ago=STALE_THRESHOLD_DAYS + 1)
    pending.improvement_status = "pending_review"
    await db.commit()

    report = await SkillCurator(db).run()
    assert report.scanned == 0


@pytest.mark.asyncio
async def test_validated_skill_is_not_protected(db: AsyncSession):
    """Validated improvements are done — curator can act normally."""
    validated = _mk(db, name="val", status=SkillStatus.ACTIVE,
                    last_used_days_ago=STALE_THRESHOLD_DAYS + 1)
    validated.improvement_status = "validated"
    await db.commit()

    report = await SkillCurator(db).run()
    assert report.scanned == 1
    assert len(report.moved_to_stale) == 1


@pytest.mark.asyncio
async def test_touch_refreshes_stale_on_use(db: AsyncSession):
    skill = _mk(db, name="g", status=SkillStatus.STALE,
                last_used_days_ago=STALE_THRESHOLD_DAYS + 1)
    await db.commit()

    await SkillCurator(db).touch(skill.id)
    await db.refresh(skill)
    assert skill.status == SkillStatus.ACTIVE
    assert skill.last_used_at is not None
