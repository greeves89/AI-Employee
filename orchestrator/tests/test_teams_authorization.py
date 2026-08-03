"""Tests for team-access authorization (fixes an IDOR on GET /teams/{id}/tasks:
any authenticated user/agent could read any team's task prompts/results just
by knowing the team_id, with no membership/ownership check at all)."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.teams import _is_team_member


def _team(member_agent_ids=None, lead_agent_id=None):
    return SimpleNamespace(member_agent_ids=member_agent_ids or [], lead_agent_id=lead_agent_id)


def _agent_principal(agent_id: str):
    return SimpleNamespace(id=agent_id, principal_type="agent")


def _human_user(user_id: str = "user-1", role=None):
    return SimpleNamespace(id=user_id, role=role)


class IsTeamMemberTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_that_is_a_member_is_allowed(self):
        team = _team(member_agent_ids=["agent-a", "agent-b"], lead_agent_id="agent-a")
        allowed = await _is_team_member(team, _agent_principal("agent-b"), db=AsyncMock())
        self.assertTrue(allowed)

    async def test_agent_that_is_lead_is_allowed(self):
        team = _team(member_agent_ids=["agent-a", "agent-b"], lead_agent_id="agent-a")
        allowed = await _is_team_member(team, _agent_principal("agent-a"), db=AsyncMock())
        self.assertTrue(allowed)

    async def test_agent_not_in_team_is_denied(self):
        team = _team(member_agent_ids=["agent-a", "agent-b"], lead_agent_id="agent-a")
        allowed = await _is_team_member(team, _agent_principal("agent-outsider"), db=AsyncMock())
        self.assertFalse(allowed)

    async def test_admin_user_is_always_allowed(self):
        team = _team(member_agent_ids=["agent-a"], lead_agent_id="agent-a")
        with patch("app.api.teams._get_user_agent_ids", new=AsyncMock(return_value=None)):
            allowed = await _is_team_member(team, _human_user(), db=AsyncMock())
        self.assertTrue(allowed)

    async def test_human_owning_a_member_agent_is_allowed(self):
        team = _team(member_agent_ids=["agent-a", "agent-b"], lead_agent_id="agent-a")
        with patch("app.api.teams._get_user_agent_ids", new=AsyncMock(return_value=["agent-b", "agent-c"])):
            allowed = await _is_team_member(team, _human_user(), db=AsyncMock())
        self.assertTrue(allowed)

    async def test_human_owning_none_of_the_team_is_denied(self):
        """The actual IDOR: a user with no relation to this team must not see it."""
        team = _team(member_agent_ids=["agent-a", "agent-b"], lead_agent_id="agent-a")
        with patch("app.api.teams._get_user_agent_ids", new=AsyncMock(return_value=["agent-z"])):
            allowed = await _is_team_member(team, _human_user(), db=AsyncMock())
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
