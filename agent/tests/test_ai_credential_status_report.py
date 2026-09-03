import unittest
from unittest.mock import AsyncMock, patch


class AgentAiCredentialStatusReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_auth_failure_reports_auth_failed_from_runner_path(self):
        from app.agent_runner import AgentRunner

        runner = AgentRunner(log_publisher=AsyncMock())
        runner._execute_task_once = AsyncMock(return_value={
            "status": "error",
            "error": "401 Unauthorized: token_expired",
        })

        with patch("app.config.wait_for_new_oauth_token", AsyncMock()), \
             patch("app.agent_runner.report_result_status", AsyncMock()) as report:
            result = await runner.execute_task("t1", "do it")

        self.assertEqual(result["status"], "error")
        report.assert_awaited_once_with({
            "status": "error",
            "error": "401 Unauthorized: token_expired",
        })

    async def test_task_success_reports_ok_from_runner_path(self):
        from app.agent_runner import AgentRunner

        runner = AgentRunner(log_publisher=AsyncMock())
        runner._execute_task_once = AsyncMock(return_value={
            "status": "completed",
            "result": "done",
        })

        with patch("app.agent_runner.report_result_status", AsyncMock()) as report:
            result = await runner.execute_task("t1", "do it")

        self.assertEqual(result["status"], "completed")
        report.assert_awaited_once_with({"status": "completed", "result": "done"})


class StatusClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_error_maps_to_auth_failed(self):
        from app.ai_credential_status import report_result_status

        with patch("app.ai_credential_status.report_ai_credential_status", AsyncMock()) as report:
            await report_result_status({
                "status": "error",
                "error": "refresh_token_reused: 401",
            })
        report.assert_awaited_once_with("auth_failed")

    async def test_success_maps_to_ok(self):
        from app.ai_credential_status import report_result_status

        with patch("app.ai_credential_status.report_ai_credential_status", AsyncMock()) as report:
            await report_result_status({"status": "completed"})
        report.assert_awaited_once_with("ok")

    async def test_non_auth_error_does_not_clear_previous_auth_failure(self):
        from app.ai_credential_status import report_result_status

        with patch("app.ai_credential_status.report_ai_credential_status", AsyncMock()) as report:
            await report_result_status({
                "status": "error",
                "error": "filesystem quota exceeded",
            })
        report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
