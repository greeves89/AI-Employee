"""Tests for the Team model (Teams feature — Task 1)."""


def test_team_imports_and_constructs():
    """The Team model imports and can be constructed with minimal args."""
    from app.models.team import Team

    t = Team(id="x", name="Dev")
    assert t.id == "x"
    assert t.name == "Dev"


def test_team_defaults():
    """Column defaults are applied on flush; pre-flush the instance may carry None."""
    from app.models.team import Team

    t = Team(id="x", name="Dev")
    # member_agent_ids default=list applies on flush; pre-flush it is None
    assert t.member_agent_ids in (None, [])
    assert t.lead_agent_id is None


def test_team_tablename():
    """The Team model maps to the 'teams' table."""
    from app.models.team import Team

    assert Team.__tablename__ == "teams"


# ─── Task 2: /teams CRUD API (mocked-DB unit tests) ──────────────

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException


def _exec_returns(value):
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    res.scalars.return_value.all.return_value = value if isinstance(value, list) else []
    return res


@pytest.mark.asyncio
async def test_create_team_builds_object():
    from app.api.teams import create_team, CreateTeam
    db = AsyncMock()
    user = MagicMock(email="me@x.de")
    out = await create_team(CreateTeam(name="Dev", member_agent_ids=["a1"]), user=user, db=db)
    assert out["name"] == "Dev"
    assert out["member_agent_ids"] == ["a1"]
    assert out["created_by"] == "me@x.de"
    db.add.assert_called_once()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_create_team_rejects_bad_lead():
    from app.api.teams import create_team, CreateTeam
    with pytest.raises(HTTPException) as e:
        await create_team(CreateTeam(name="D", member_agent_ids=["a1"], lead_agent_id="ghost"),
                          user=MagicMock(email="m"), db=AsyncMock())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_get_team_404_when_missing():
    from app.api.teams import get_team
    db = AsyncMock()
    db.execute.return_value = _exec_returns(None)
    with pytest.raises(HTTPException) as e:
        await get_team("nope", user=MagicMock(), db=db)
    assert e.value.status_code == 404
