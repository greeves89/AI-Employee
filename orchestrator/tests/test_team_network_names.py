"""Regression tests: a deleted agent must not surface as a raw ID/"Unknown"
in team-communication views — reported by a customer scrolling the network
view's conversation tiles (Kundenrueckmeldung vom 18.08.2026).

Covers the three call sites identified: /team/messages (network view
connections), _list_my_team (an agent's own teammate awareness), and
meeting_rooms.get_room (room participant names).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _exec_result(items):
    res = MagicMock()
    res.scalars.return_value.all.return_value = items
    return res


@pytest.mark.asyncio
async def test_get_agent_messages_names_deleted_agent_clearly():
    """A connection between a live agent and a deleted one gets a real name on
    one side and a clear "Gelöschter Agent" label on the other — never the
    raw UUID, which is what read as "Unknown" to the customer."""
    from app.api.agents import get_agent_messages

    now = datetime.now(timezone.utc)
    msg = SimpleNamespace(
        from_agent_id="live-1",
        to_agent_id="deleted-1",
        from_agent_name="Alice",
        timestamp=now,
        text="hallo",
        message_id="m1",
        message_type="text",
        reply_to=None,
    )

    messages_res = _exec_result([msg])
    # Only the still-existing agent comes back from the name lookup.
    agents_res = _exec_result([SimpleNamespace(id="live-1", name="Alice")])

    db = AsyncMock()
    db.execute.side_effect = [messages_res, agents_res]

    user = SimpleNamespace(id="admin1", role="admin")
    out = await get_agent_messages(minutes=60, user=user, db=db)

    assert len(out["connections"]) == 1
    conn = out["connections"][0]
    # connections key on the sorted (from, to) pair, not the message's literal
    # direction — assert on the set of names rather than a fixed side.
    assert {conn["from_name"], conn["to_name"]} == {"Alice", "Gelöschter Agent"}
    # Never the bare UUID standing in as a name.
    assert "deleted-1" not in (conn["from_name"], conn["to_name"])


@pytest.mark.asyncio
async def test_list_my_team_excludes_stale_members():
    """A team member ID with no matching Agent row (deleted) is dropped from
    the roster the agent sees for itself — not listed as a nameless colleague."""
    from app.api.mcp_agent import _list_my_team
    from app.models.team import Team

    t1 = Team(id="t1", name="A", member_agent_ids=["me", "x", "gone"], lead_agent_id="me", is_active=True)

    teams_res = MagicMock()
    teams_res.scalars.return_value.all.return_value = [t1]
    agents_res = MagicMock()
    agents_res.scalars.return_value.all.return_value = [
        SimpleNamespace(id="me", name="Me", config={"role": "dev"}),
        SimpleNamespace(id="x", name="Xavier", config={}),
        # "gone" deliberately absent — simulates a deleted teammate.
    ]
    db = AsyncMock()
    db.execute.side_effect = [teams_res, agents_res]

    agent = SimpleNamespace(id="me")
    out = await _list_my_team(agent, db)

    member_ids = {m["id"] for m in out[0]["members"]}
    assert member_ids == {"me", "x"}
    assert "gone" not in member_ids


@pytest.mark.asyncio
async def test_meeting_room_names_deleted_agent_clearly(monkeypatch):
    """get_room labels a deleted participant instead of falling back to the
    bare agent ID."""
    from app.api import meeting_rooms

    room = SimpleNamespace(
        id="room1",
        agent_ids=["live-1", "deleted-1"],
        name="Standup",
        topic="t",
        state="idle",
        current_turn=None,
        rounds_completed=0,
        max_rounds=5,
        stages_config=None,
        use_moderator=False,
        deliverable=None,
        deliverable_integrated=False,
        messages=[],
        created_at=None,
        scheduled_for=None,
    )

    async def fake_scalar(query):
        # First call resolves the room itself, then one call per agent_id.
        if not hasattr(fake_scalar, "calls"):
            fake_scalar.calls = 0
        fake_scalar.calls += 1
        if fake_scalar.calls == 1:
            return room
        if fake_scalar.calls == 2:
            return SimpleNamespace(id="live-1", name="Alice")
        return None  # "deleted-1" no longer exists

    db = AsyncMock()
    db.scalar = fake_scalar

    user = SimpleNamespace(id="admin1", role="admin")
    monkeypatch.setattr(meeting_rooms, "_authorize_room", AsyncMock(return_value=None))

    out = await meeting_rooms.get_room(room_id="room1", user=user, db=db)

    assert out["agent_names"]["live-1"] == "Alice"
    assert out["agent_names"]["deleted-1"] == "Gelöschter Agent"
