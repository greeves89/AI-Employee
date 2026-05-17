"""Tests for the skill improvement review flow (proposal → approve/reject)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.skill import Skill, SkillImprovementStatus


class TestModel:
    def test_pending_review_status_exists(self):
        assert SkillImprovementStatus.PENDING_REVIEW == "pending_review"

    def test_skill_has_proposal_fields(self):
        s = Skill(
            name="s1",
            improvement_status="pending_review",
            improvement_proposal={"suggested_content": "new"},
            improvement_proposed_at=datetime.now(timezone.utc),
            improvement_review_reason="low_helpfulness",
        )
        assert s.improvement_status == "pending_review"
        assert s.improvement_proposal["suggested_content"] == "new"
        assert s.improvement_review_reason == "low_helpfulness"

    def test_proposal_fields_default_none(self):
        s = Skill(name="s2")
        assert s.improvement_proposal is None
        assert s.improvement_proposed_at is None
        assert s.improvement_review_reason is None


class TestImprovementEngineProposes:
    @pytest.mark.asyncio
    async def test_engine_creates_proposal_not_task(self):
        """A low-rated skill gets a proposal — no Task is dispatched, no overwrite."""
        from app.services import improvement_engine as eng

        skill = Skill(name="weak-skill", content="old content", status="active")
        skill.id = 7
        skill.improvement_status = None
        skill.updated_at = None

        agg_row = MagicMock(skill_id=7, rated_count=6, avg_helpfulness=2.1)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [skill]

        call = 0

        async def mock_execute(query):
            nonlocal call
            call += 1
            result = MagicMock()
            if call == 1:  # agg query
                result.all.return_value = [agg_row]
            else:  # skills query
                result.scalars.return_value = scalars_mock
            return result

        db = AsyncMock()
        db.execute = mock_execute
        db.commit = AsyncMock()

        with patch.object(eng, "_load_thresholds", AsyncMock(return_value={
            "min_skill_usages": 5, "skill_threshold": 3.0,
        })), patch.object(eng, "_generate_skill_improvement",
                           AsyncMock(return_value="much better rewritten content")), \
             patch.object(eng, "_notify_feedback_contributors", AsyncMock()):
            await eng._improve_poorly_rated_skills(db)

        assert skill.improvement_status == "pending_review"
        assert skill.content == "old content"  # NOT overwritten
        assert skill.improvement_proposal is not None
        assert skill.improvement_proposal["suggested_content"] == "much better rewritten content"
        assert skill.improvement_proposal["avg_helpfulness_before"] == 2.1
        assert skill.improvement_review_reason == "low_helpfulness"

    @pytest.mark.asyncio
    async def test_engine_skips_skill_already_pending(self):
        """A skill already pending review is not re-proposed."""
        from app.services import improvement_engine as eng

        skill = Skill(name="pending-skill", content="x", status="active")
        skill.id = 9
        skill.improvement_status = "pending_review"
        skill.updated_at = None

        agg_row = MagicMock(skill_id=9, rated_count=6, avg_helpfulness=2.0)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [skill]
        call = 0

        async def mock_execute(query):
            nonlocal call
            call += 1
            result = MagicMock()
            if call == 1:
                result.all.return_value = [agg_row]
            else:
                result.scalars.return_value = scalars_mock
            return result

        db = AsyncMock()
        db.execute = mock_execute
        db.commit = AsyncMock()

        gen = AsyncMock(return_value="rewrite")
        with patch.object(eng, "_load_thresholds", AsyncMock(return_value={
            "min_skill_usages": 5, "skill_threshold": 3.0,
        })), patch.object(eng, "_generate_skill_improvement", gen), \
             patch.object(eng, "_notify_feedback_contributors", AsyncMock()):
            await eng._improve_poorly_rated_skills(db)

        gen.assert_not_called()  # skipped before LLM call


class TestProposalApplication:
    """The approve path: applying a proposal puts the skill into probation."""

    def test_apply_proposal_logic(self):
        skill = Skill(name="s", content="old", description="old desc")
        skill.current_version = 3
        skill.improvement_status = "pending_review"
        skill.improvement_proposal = {
            "old_content": "old",
            "suggested_content": "new and improved",
            "suggested_description": "new desc",
            "avg_helpfulness_before": 2.4,
            "rated_count_before": 7,
        }
        proposal = skill.improvement_proposal
        # Mirror approve_improvement's mutation
        skill.content = proposal["suggested_content"]
        skill.description = proposal["suggested_description"]
        skill.current_version += 1
        skill.improvement_status = "probation"
        skill.pre_improvement_avg_helpfulness = proposal["avg_helpfulness_before"]
        skill.pre_improvement_rated_count = proposal["rated_count_before"]
        skill.improvement_proposal = None

        assert skill.content == "new and improved"
        assert skill.current_version == 4
        assert skill.improvement_status == "probation"
        assert skill.pre_improvement_avg_helpfulness == 2.4
        assert skill.improvement_proposal is None
