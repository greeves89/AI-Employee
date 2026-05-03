"""Tests for skill A/B validation after auto-improvement (issue #148)."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.skill import Skill, SkillVersion, SkillTaskUsage, SkillImprovementStatus


class TestSkillVersionModel:
    def test_skill_version_fields(self):
        v = SkillVersion(
            skill_id=1,
            version_number=1,
            content="old content",
            description="old desc",
            avg_helpfulness_at_snapshot=2.5,
            rated_usages_at_snapshot=10,
            created_by="agent:123",
        )
        assert v.skill_id == 1
        assert v.version_number == 1
        assert v.content == "old content"
        assert v.avg_helpfulness_at_snapshot == 2.5

    def test_skill_improvement_status_enum(self):
        assert SkillImprovementStatus.PROBATION == "probation"
        assert SkillImprovementStatus.VALIDATED == "validated"
        assert SkillImprovementStatus.ROLLED_BACK == "rolled_back"


class TestSkillProbationFields:
    def test_skill_has_probation_fields(self):
        s = Skill(
            name="test-skill",
            improvement_status="probation",
            probation_started_at=datetime.now(timezone.utc),
            pre_improvement_avg_helpfulness=2.3,
            pre_improvement_rated_count=8,
        )
        assert s.improvement_status == "probation"
        assert s.pre_improvement_avg_helpfulness == 2.3
        assert s.pre_improvement_rated_count == 8

    def test_skill_probation_default_none(self):
        s = Skill(name="test-skill-2")
        assert s.improvement_status is None
        assert s.probation_started_at is None
        assert s.pre_improvement_avg_helpfulness is None
        assert s.pre_improvement_rated_count is None


class TestImprovementEngineConstants:
    def test_probation_constants(self):
        from app.services.improvement_engine import _PROBATION_MIN_USAGES, _PROBATION_MAX_DAYS
        assert _PROBATION_MIN_USAGES == 5
        assert _PROBATION_MAX_DAYS == 14


class TestValidateProbationSkills:
    """Test the _validate_probation_skills logic without DB."""

    @pytest.mark.asyncio
    async def test_no_skills_in_probation_is_noop(self):
        from app.services.improvement_engine import _validate_probation_skills
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
        await _validate_probation_skills(db)

    @pytest.mark.asyncio
    async def test_skill_validated_when_improved(self):
        """When post-improvement avg > pre-improvement avg, skill is validated."""
        from app.services.improvement_engine import _validate_probation_skills

        skill = MagicMock(spec=Skill)
        skill.id = 1
        skill.name = "test-skill"
        skill.improvement_status = "probation"
        skill.probation_started_at = datetime.now(timezone.utc) - timedelta(days=3)
        skill.pre_improvement_avg_helpfulness = 2.5
        skill.pre_improvement_rated_count = 8
        skill.content = "new content"

        # Mock DB: return skill in probation
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [skill]

        post_result = MagicMock()
        post_result.count = 6
        post_result.avg_h = 3.8  # better than 2.5

        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.scalars.return_value = scalars_mock
                return result
            else:
                result = MagicMock()
                result.one.return_value = post_result
                return result

        db = AsyncMock()
        db.execute = mock_execute
        db.flush = AsyncMock()

        await _validate_probation_skills(db)

        assert skill.improvement_status == "validated"
        assert skill.probation_started_at is None

    @pytest.mark.asyncio
    async def test_skill_rolled_back_when_worse(self):
        """When post-improvement avg <= pre-improvement avg, skill is rolled back."""
        from app.services.improvement_engine import _validate_probation_skills

        skill = MagicMock(spec=Skill)
        skill.id = 1
        skill.name = "test-skill"
        skill.improvement_status = "probation"
        skill.probation_started_at = datetime.now(timezone.utc) - timedelta(days=3)
        skill.pre_improvement_avg_helpfulness = 2.5
        skill.pre_improvement_rated_count = 8
        skill.content = "new content"
        skill.description = "desc"

        # Version snapshot for rollback
        version = MagicMock(spec=SkillVersion)
        version.version_number = 1
        version.content = "original content"
        version.description = "original desc"

        post_result = MagicMock()
        post_result.count = 6
        post_result.avg_h = 2.0  # worse than 2.5

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [skill]

        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.scalars.return_value = scalars_mock
                return result
            elif call_count == 2:
                result = MagicMock()
                result.one.return_value = post_result
                return result
            else:
                result = MagicMock()
                result.scalar_one_or_none.return_value = version
                return result

        db = AsyncMock()
        db.execute = mock_execute
        db.flush = AsyncMock()

        await _validate_probation_skills(db)

        assert skill.improvement_status == "rolled_back"
        assert skill.content == "original content"
        assert skill.probation_started_at is None

    @pytest.mark.asyncio
    async def test_skip_if_insufficient_data_within_window(self):
        """If < 5 usages and < 14 days, skip (still in probation)."""
        from app.services.improvement_engine import _validate_probation_skills

        skill = MagicMock(spec=Skill)
        skill.id = 1
        skill.name = "test-skill"
        skill.improvement_status = "probation"
        skill.probation_started_at = datetime.now(timezone.utc) - timedelta(days=2)
        skill.pre_improvement_avg_helpfulness = 2.5
        skill.pre_improvement_rated_count = 8

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [skill]

        post_result = MagicMock()
        post_result.count = 2  # not enough
        post_result.avg_h = 4.0

        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.scalars.return_value = scalars_mock
                return result
            else:
                result = MagicMock()
                result.one.return_value = post_result
                return result

        db = AsyncMock()
        db.execute = mock_execute
        db.flush = AsyncMock()

        await _validate_probation_skills(db)

        # Should still be in probation — not validated yet
        assert skill.improvement_status == "probation"
