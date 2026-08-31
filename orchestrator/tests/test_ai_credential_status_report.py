import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class AgentAiCredentialStatusEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failed_report_calls_mark_status_for_owner_harness(self):
        from app.api.agents import AiCredentialStatusReport, report_ai_credential_status

        manager = SimpleNamespace(
            _get_agent=AsyncMock(return_value=SimpleNamespace(
                user_id="user-1",
                mode="codex_cli",
                config={},
                llm_config=None,
            ))
        )

        db = object()

        with patch("app.api.my_ai_credentials.mark_status", AsyncMock()) as mark:
            payload = await report_ai_credential_status(
                "agent-1",
                AiCredentialStatusReport(status="auth_failed"),
                db=db,
                manager=manager,
                agent_auth={"agent_id": "agent-1"},
            )

        self.assertEqual(payload, {"status": "auth_failed", "harness": "codex"})
        mark.assert_awaited_once_with(db, "user-1", "codex", "auth_failed")

    async def test_ok_report_calls_mark_status_to_recover_after_failure(self):
        from app.api.agents import AiCredentialStatusReport, report_ai_credential_status

        db = object()
        manager = SimpleNamespace(
            _get_agent=AsyncMock(return_value=SimpleNamespace(
                user_id="user-1",
                mode="claude_code",
                config={"model_provider": "anthropic"},
                llm_config=None,
            ))
        )

        with patch("app.api.my_ai_credentials.mark_status", AsyncMock()) as mark:
            payload = await report_ai_credential_status(
                "agent-1",
                AiCredentialStatusReport(status="ok"),
                db=db,
                manager=manager,
                agent_auth={"agent_id": "agent-1"},
            )

        self.assertEqual(payload, {"status": "ok", "harness": "claude_code"})
        mark.assert_awaited_once_with(db, "user-1", "claude_code", "ok")

    async def test_agent_token_cannot_report_for_another_agent(self):
        from fastapi import HTTPException
        from app.api.agents import AiCredentialStatusReport, report_ai_credential_status

        with self.assertRaises(HTTPException) as ctx:
            await report_ai_credential_status(
                "agent-1",
                AiCredentialStatusReport(status="ok"),
                db=object(),
                manager=SimpleNamespace(),
                agent_auth={"agent_id": "agent-2"},
            )

        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
