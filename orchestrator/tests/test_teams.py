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


# ─── Task 3: member + lead management (mocked-DB unit tests) ──────


@pytest.mark.asyncio
async def test_set_lead_rejects_non_member():
    from app.api.teams import set_lead, SetLead, Team
    from unittest.mock import AsyncMock, MagicMock
    t = Team(id="t1", name="T", member_agent_ids=["a1"], lead_agent_id=None, is_active=True)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=t))
    with pytest.raises(HTTPException) as e:
        await set_lead("t1", SetLead(lead_agent_id="ghost"), user=MagicMock(), db=db)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_set_lead_ok_for_member():
    from app.api.teams import set_lead, SetLead, Team
    from unittest.mock import AsyncMock, MagicMock
    t = Team(id="t1", name="T", member_agent_ids=["a1", "lead1"], lead_agent_id=None, is_active=True)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=t))
    out = await set_lead("t1", SetLead(lead_agent_id="lead1"), user=MagicMock(), db=db)
    assert out["lead_agent_id"] == "lead1"


@pytest.mark.asyncio
async def test_remove_member_clears_lead():
    from app.api.teams import change_members, MembersChange, Team
    from unittest.mock import AsyncMock, MagicMock
    t = Team(id="t1", name="T", member_agent_ids=["a1", "lead1"], lead_agent_id="lead1", is_active=True)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=t))
    out = await change_members("t1", MembersChange(remove=["lead1"]), user=MagicMock(), db=db)
    assert "lead1" not in out["member_agent_ids"]
    assert out["lead_agent_id"] is None


# ─── Task 4: delegate-to-team via lead routing (mocked-DB unit tests) ──


@pytest.mark.asyncio
async def test_delegate_requires_lead():
    from app.api.teams import delegate_to_team, DelegateTask
    from app.models.team import Team
    from unittest.mock import AsyncMock, MagicMock
    t = Team(id="t1", name="T", member_agent_ids=["a1"], lead_agent_id=None, is_active=True)
    db = AsyncMock(); db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=t))
    with pytest.raises(HTTPException) as e:
        await delegate_to_team("t1", DelegateTask(title="x", prompt="do"),
                               request=MagicMock(), router_=AsyncMock(), user=MagicMock(), db=db)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_delegate_creates_lead_task_with_roster(monkeypatch):
    import app.api.teams as teams_mod
    from app.api.teams import delegate_to_team, DelegateTask
    from app.models.team import Team
    from unittest.mock import AsyncMock, MagicMock
    t = Team(id="t1", name="T", member_agent_ids=["lead1", "m2"], lead_agent_id="lead1", is_active=True)
    db = AsyncMock(); db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=t))
    # avoid real container/agent lookups
    async def fake_roster(team, db_, request): return "- Lead [lead1] (LEAD): builds\n- M2 [m2]: tests"
    monkeypatch.setattr(teams_mod, "_team_roster", fake_roster)
    captured = {}
    class _Task: id = "task1"; status = "queued"
    router_ = AsyncMock()
    async def fake_create(**kw): captured.update(kw); return _Task()
    router_.create_and_route_task = fake_create
    out = await delegate_to_team("t1", DelegateTask(title="build", prompt="do x"),
                                 request=MagicMock(), router_=router_, user=MagicMock(), db=db)
    assert out["task_id"] == "task1" and out["lead_agent_id"] == "lead1"
    assert captured["agent_id"] == "lead1"
    assert "Team-Roster" in captured["prompt"] and "do x" in captured["prompt"]
    assert captured["metadata"]["team_id"] == "t1"
