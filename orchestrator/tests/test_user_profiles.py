"""Tests for Adaptive User Profiles (issue #140)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.user_profile import UserProfile, UserProfileEvent
from app.services.profile_extractor import (
    _classify_dimension,
    _extract_key_value,
    generate_profile_summary,
)


class TestUserProfileModel:
    def test_user_profile_fields(self):
        p = UserProfile(
            user_id="user-1",
            profile_version=3,
            dimensions={"communication": {"verbosity": {"value": "concise", "confidence": 0.9}}},
        )
        assert p.user_id == "user-1"
        assert p.profile_version == 3
        assert "communication" in p.dimensions

    def test_user_profile_defaults(self):
        p = UserProfile(user_id="user-2", profile_version=1, dimensions={})
        assert p.profile_version == 1
        assert p.dimensions == {}
        assert p.last_extracted_at is None

    def test_user_profile_event_fields(self):
        ev = UserProfileEvent(
            user_id="user-1",
            dimension="communication",
            key="verbosity",
            old_value="detailed",
            new_value="concise",
            source="extraction",
            confidence=0.85,
        )
        assert ev.dimension == "communication"
        assert ev.key == "verbosity"
        assert ev.old_value == "detailed"
        assert ev.new_value == "concise"
        assert ev.source == "extraction"

    def test_user_profile_event_no_old_value(self):
        ev = UserProfileEvent(
            user_id="user-1",
            dimension="technical",
            key="language",
            new_value="typescript",
        )
        assert ev.old_value is None


class TestClassifyDimension:
    def test_communication_keywords(self):
        assert _classify_dimension("Bitte antworte concise und kurz") == "communication"

    def test_technical_keywords(self):
        assert _classify_dimension("Use python and fastapi for the backend") == "technical"

    def test_workflow_keywords(self):
        assert _classify_dimension("Always create a PR before merging the branch") == "workflow"

    def test_schedule_keywords(self):
        assert _classify_dimension("Send me a daily reminder in the morning via telegram") == "schedule"

    def test_no_match(self):
        assert _classify_dimension("The weather is nice today") is None

    def test_mixed_picks_highest(self):
        result = _classify_dimension("Use python testing framework for integration test unit test")
        assert result == "technical"


class TestExtractKeyValue:
    def test_colon_format(self):
        key, val = _extract_key_value("preference", "pref", "Language: German")
        assert key == "language"
        assert val == "German"

    def test_long_content_fallback(self):
        long_content = "A" * 600
        key, val = _extract_key_value("preference", "mykey", long_content)
        assert key == "mykey"
        assert len(val) == 200


class TestGenerateProfileSummary:
    def test_empty_profile(self):
        p = UserProfile(user_id="u1", dimensions={})
        assert generate_profile_summary(p) == ""

    def test_low_confidence_filtered(self):
        p = UserProfile(user_id="u1", dimensions={
            "communication": {
                "verbosity": {"value": "concise", "confidence": 0.3},
            }
        })
        assert generate_profile_summary(p) == ""

    def test_high_confidence_included(self):
        p = UserProfile(user_id="u1", dimensions={
            "communication": {
                "verbosity": {"value": "concise", "confidence": 0.9},
                "tone": {"value": "informal", "confidence": 0.7},
            }
        })
        summary = generate_profile_summary(p)
        assert "Communication" in summary
        assert "verbosity" in summary
        assert "concise" in summary
        assert "tone" in summary

    def test_multiple_dimensions(self):
        p = UserProfile(user_id="u1", dimensions={
            "communication": {"tone": {"value": "informal", "confidence": 0.8}},
            "technical": {"language": {"value": "python", "confidence": 0.95}},
        })
        summary = generate_profile_summary(p)
        assert "Communication" in summary
        assert "Technical" in summary

    def test_truncates_long_values(self):
        p = UserProfile(user_id="u1", dimensions={
            "workflow": {"process": {"value": "A" * 200, "confidence": 0.9}},
        })
        summary = generate_profile_summary(p)
        assert "..." in summary


class TestProfileExtractorAsync:
    @pytest.mark.asyncio
    async def test_get_or_create_profile_new(self):
        from app.services.profile_extractor import get_or_create_profile

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        profile = await get_or_create_profile(db, "user-new")
        assert profile.user_id == "user-new"
        assert profile.dimensions == {}
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_profile_existing(self):
        from app.services.profile_extractor import get_or_create_profile

        existing = UserProfile(user_id="user-1", profile_version=5, dimensions={"a": "b"})
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        profile = await get_or_create_profile(db, "user-1")
        assert profile.profile_version == 5
        assert profile.dimensions == {"a": "b"}

    @pytest.mark.asyncio
    async def test_update_dimension(self):
        from app.services.profile_extractor import update_dimension

        existing = UserProfile(user_id="user-1", profile_version=1, dimensions={})
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        profile = await update_dimension(db, "user-1", "communication", "verbosity", "concise", 1.0)
        assert profile.profile_version == 2
        assert "communication" in profile.dimensions
        assert profile.dimensions["communication"]["verbosity"]["value"] == "concise"
        assert profile.dimensions["communication"]["verbosity"]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_delete_dimension_whole(self):
        from app.services.profile_extractor import delete_dimension

        existing = UserProfile(
            user_id="user-1", profile_version=3,
            dimensions={"communication": {"verbosity": {"value": "concise"}}, "technical": {"lang": {"value": "py"}}}
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        profile = await delete_dimension(db, "user-1", "communication")
        assert "communication" not in profile.dimensions
        assert "technical" in profile.dimensions
        assert profile.profile_version == 4

    @pytest.mark.asyncio
    async def test_delete_dimension_single_key(self):
        from app.services.profile_extractor import delete_dimension

        existing = UserProfile(
            user_id="user-1", profile_version=2,
            dimensions={"communication": {"verbosity": {"value": "concise"}, "tone": {"value": "informal"}}}
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        profile = await delete_dimension(db, "user-1", "communication", "verbosity")
        assert "verbosity" not in profile.dimensions["communication"]
        assert "tone" in profile.dimensions["communication"]
