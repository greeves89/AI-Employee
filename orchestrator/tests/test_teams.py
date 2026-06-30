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
