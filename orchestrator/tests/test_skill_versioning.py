"""Tests for Skill Versioning & Rollback (Issue #151)."""
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.skill import Skill, SkillVersion, SkillTaskUsage, SkillCategory, SkillStatus
from app.models.task import Task, TaskStatus


_TABLES = [
    Base.metadata.tables["tasks"],
    Base.metadata.tables["skills"],
    Base.metadata.tables["skill_versions"],
    Base.metadata.tables["skill_task_usages"],
]


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    Base.metadata.create_all(engine, tables=_TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def sample_skill(db_session: Session) -> Skill:
    skill = Skill(
        id=1,
        name="test-skill",
        description="Test skill for versioning",
        content="# Step 1\nDo the thing",
        category=SkillCategory.WORKFLOW,
        status=SkillStatus.ACTIVE,
        created_by="user",
        current_version=1,
        usage_count=10,
    )
    db_session.add(skill)
    db_session.commit()
    return skill


class TestSkillVersionModel:
    def test_create_version(self, db_session: Session, sample_skill: Skill):
        version = SkillVersion(
            skill_id=sample_skill.id,
            version_number=1,
            content=sample_skill.content,
            description=sample_skill.description,
            avg_helpfulness_at_snapshot=3.5,
            usage_count_at_snapshot=10,
            created_by="user",
            change_reason="Manual update",
        )
        db_session.add(version)
        db_session.commit()

        assert version.id is not None
        assert version.version_number == 1
        assert version.content == "# Step 1\nDo the thing"
        assert version.avg_helpfulness_at_snapshot == 3.5

    def test_multiple_versions(self, db_session: Session, sample_skill: Skill):
        for i in range(1, 4):
            db_session.add(SkillVersion(
                skill_id=sample_skill.id,
                version_number=i,
                content=f"Version {i} content",
                description=f"Desc v{i}",
                created_by="agent:123" if i > 1 else "user",
            ))
        db_session.commit()

        versions = db_session.query(SkillVersion).filter_by(skill_id=sample_skill.id).order_by(SkillVersion.version_number).all()
        assert len(versions) == 3
        assert versions[0].version_number == 1
        assert versions[2].version_number == 3

    def test_version_links_to_skill(self, db_session: Session, sample_skill: Skill):
        db_session.add(SkillVersion(
            skill_id=sample_skill.id,
            version_number=1,
            content="old content",
            created_by="user",
        ))
        db_session.commit()
        v = db_session.query(SkillVersion).filter_by(skill_id=sample_skill.id).first()
        assert v is not None
        assert v.skill_id == sample_skill.id


class TestSkillCurrentVersion:
    def test_default_current_version(self, db_session: Session):
        skill = Skill(
            name="new-skill",
            content="content",
            category=SkillCategory.ROUTINE,
            status=SkillStatus.ACTIVE,
            current_version=1,
        )
        db_session.add(skill)
        db_session.commit()
        assert skill.current_version == 1

    def test_increment_version(self, db_session: Session, sample_skill: Skill):
        assert sample_skill.current_version == 1
        sample_skill.current_version = 2
        db_session.commit()
        db_session.refresh(sample_skill)
        assert sample_skill.current_version == 2


class TestSkillTaskUsageVersion:
    @pytest.fixture(autouse=True)
    def _create_task(self, db_session: Session):
        db_session.add(Task(id="task-abc", title="t", prompt="p", agent_id="agent-1", status=TaskStatus.RUNNING))
        db_session.add(Task(id="task-xyz", title="t", prompt="p", agent_id="agent-1", status=TaskStatus.RUNNING))
        db_session.commit()

    def test_usage_tracks_version(self, db_session: Session, sample_skill: Skill):
        usage = SkillTaskUsage(
            skill_id=sample_skill.id,
            task_id="task-abc",
            agent_id="agent-1",
            skill_version=3,
            skill_helpfulness=4,
        )
        db_session.add(usage)
        db_session.commit()
        assert usage.skill_version == 3

    def test_usage_version_nullable(self, db_session: Session, sample_skill: Skill):
        usage = SkillTaskUsage(
            skill_id=sample_skill.id,
            task_id="task-xyz",
            agent_id="agent-1",
        )
        db_session.add(usage)
        db_session.commit()
        assert usage.skill_version is None


class TestVersionSnapshotWorkflow:
    def test_snapshot_before_update(self, db_session: Session, sample_skill: Skill):
        """Simulate the snapshot-before-update workflow."""
        old_content = sample_skill.content
        old_version = sample_skill.current_version

        version = SkillVersion(
            skill_id=sample_skill.id,
            version_number=old_version,
            content=old_content,
            description=sample_skill.description,
            usage_count_at_snapshot=sample_skill.usage_count,
            created_by="user",
            change_reason="Before manual update",
        )
        db_session.add(version)
        sample_skill.current_version = old_version + 1
        sample_skill.content = "# Step 1\nNew improved content"
        db_session.commit()

        assert sample_skill.current_version == 2
        assert sample_skill.content == "# Step 1\nNew improved content"

        saved_version = db_session.query(SkillVersion).filter_by(skill_id=sample_skill.id).first()
        assert saved_version.content == "# Step 1\nDo the thing"
        assert saved_version.version_number == 1

    def test_rollback_creates_snapshot(self, db_session: Session, sample_skill: Skill):
        """Simulate rollback: snapshot current → restore old."""
        # v1 snapshot
        db_session.add(SkillVersion(
            skill_id=sample_skill.id, version_number=1,
            content="v1 content", description="v1 desc", created_by="user",
        ))
        sample_skill.current_version = 2
        sample_skill.content = "v2 content"
        db_session.commit()

        # Rollback to v1
        v1 = db_session.query(SkillVersion).filter_by(
            skill_id=sample_skill.id, version_number=1
        ).first()

        db_session.add(SkillVersion(
            skill_id=sample_skill.id, version_number=2,
            content=sample_skill.content, description=sample_skill.description,
            created_by="rollback",
            change_reason=f"Rolled back to version {v1.version_number}",
        ))
        sample_skill.current_version = 3
        sample_skill.content = v1.content
        db_session.commit()

        assert sample_skill.content == "v1 content"
        assert sample_skill.current_version == 3

        all_versions = db_session.query(SkillVersion).filter_by(
            skill_id=sample_skill.id
        ).order_by(SkillVersion.version_number).all()
        assert len(all_versions) == 2
        assert all_versions[1].created_by == "rollback"
