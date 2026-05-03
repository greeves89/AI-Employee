"""Tests for user feedback loop notifications (Issue #152)."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON, Boolean, Text
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase


class Base(DeclarativeBase):
    pass


# Minimal table stubs for SQLite testing
_NOTIFICATION_ROWS: list = []


@pytest.fixture
def mock_db():
    """Create a mock async DB session."""
    db = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_notify_feedback_contributors_creates_notifications(mock_db):
    """When a skill improvement is dispatched, users who gave low ratings get notified."""
    from app.services.improvement_engine import _notify_feedback_contributors
    from app.models.skill import Skill

    skill = MagicMock(spec=Skill)
    skill.id = 42
    skill.name = "deploy-script"

    # Mock: 2 task_ids from low-rated usages
    usage_result = MagicMock()
    usage_result.all.return_value = [("task-1",), ("task-2",)]

    # Mock: 2 distinct users who rated those tasks
    rating_result = MagicMock()
    rating_result.all.return_value = [("user-a", "agent-1"), ("user-b", "agent-1")]

    mock_db.execute = AsyncMock(side_effect=[usage_result, rating_result])

    await _notify_feedback_contributors(mock_db, skill, 2.4, 7)

    # Should have added 2 notifications
    assert mock_db.add.call_count == 2
    notif1 = mock_db.add.call_args_list[0][0][0]
    assert notif1.type == "info"
    assert "deploy-script" in notif1.title
    assert notif1.meta["type"] == "skill_feedback_triggered"
    assert notif1.meta["skill_id"] == 42
    assert notif1.meta["user_id"] == "user-a"

    notif2 = mock_db.add.call_args_list[1][0][0]
    assert notif2.meta["user_id"] == "user-b"


@pytest.mark.asyncio
async def test_notify_feedback_contributors_no_usages(mock_db):
    """If no low-rated usages exist, no notifications are created."""
    from app.services.improvement_engine import _notify_feedback_contributors
    from app.models.skill import Skill

    skill = MagicMock(spec=Skill)
    skill.id = 10
    skill.name = "test-skill"

    usage_result = MagicMock()
    usage_result.all.return_value = []
    mock_db.execute = AsyncMock(return_value=usage_result)

    await _notify_feedback_contributors(mock_db, skill, 2.8, 5)

    # No notifications should be added
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_notify_feedback_contributors_no_ratings(mock_db):
    """If usages exist but no TaskRatings found, no notifications."""
    from app.services.improvement_engine import _notify_feedback_contributors
    from app.models.skill import Skill

    skill = MagicMock(spec=Skill)
    skill.id = 10
    skill.name = "test-skill"

    usage_result = MagicMock()
    usage_result.all.return_value = [("task-1",)]

    rating_result = MagicMock()
    rating_result.all.return_value = []

    mock_db.execute = AsyncMock(side_effect=[usage_result, rating_result])

    await _notify_feedback_contributors(mock_db, skill, 2.5, 6)

    mock_db.add.assert_not_called()
