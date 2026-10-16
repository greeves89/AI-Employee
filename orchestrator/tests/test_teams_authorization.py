"""Tests for team-access authorization (fixes an IDOR on GET /teams/{id}/tasks:
any authenticated user/agent could read any team's task prompts/results just
by knowing the team_id, with no membership/ownership check at all)."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.teams import _is_team_member


def _team(member_agent_ids=None, lead_agent_id=None):
    return SimpleNamespace(member_agent_ids=member_agent_ids or [], lead_agent_id=lead_agent_id,
                           created_by=None, geteilt_mit=[])


def _agent_principal(agent_id: str):
    return SimpleNamespace(id=agent_id, principal_type="agent")


def _human_user(user_id: str = "user-1", role=None):
    return SimpleNamespace(id=user_id, role=role, email=None)


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

    async def test_nur_der_ersteller_sieht_das_projekt(self):
        team = _team(member_agent_ids=["agent-a"], lead_agent_id="agent-a")
        team.created_by = "user-1"
        team.geteilt_mit = []
        self.assertTrue(await _is_team_member(team, _human_user("user-1"), db=AsyncMock()))

    async def test_auch_kein_admin_und_kein_besitzer_eines_mitglieds(self):
        """Projekte sind zunaechst NUR fuer den Ersteller sichtbar (Vorgabe 16.10.2026)."""
        from app.models.user import UserRole

        team = _team(member_agent_ids=["agent-a", "agent-b"], lead_agent_id="agent-a")
        team.created_by = "someone-else"
        team.geteilt_mit = []
        for wer in (_human_user("admin-1", role=UserRole.ADMIN), _human_user("owner-of-agent-b")):
            with patch("app.api.teams._get_user_agent_ids", new=AsyncMock(return_value=None)):
                self.assertFalse(await _is_team_member(team, wer, db=AsyncMock()))

    async def test_freigegeben_fuer_einen_weiteren_nutzer(self):
        team = _team(member_agent_ids=["agent-a"], lead_agent_id="agent-a")
        team.created_by = "user-1"
        team.geteilt_mit = ["user-2"]
        self.assertTrue(await _is_team_member(team, _human_user("user-2"), db=AsyncMock()))
        self.assertFalse(await _is_team_member(team, _human_user("user-3"), db=AsyncMock()))


if __name__ == "__main__":
    unittest.main()
