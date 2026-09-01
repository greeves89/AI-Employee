"""Teil D — Container-Rebuild des Agenten per Chat: ``POST /agent-apps/restart-self``.

Anders als ``agents.py``'s ``/{agent_id}/update`` (menschliches JWT + Owner-Check)
laeuft dieser Endpunkt ueber ``verify_agent_token`` — ein Agent hat keine
Nutzersitzung, um einen Aufruf ueber sich selbst zu authentifizieren. Die Tests
decken: Agent-Id kommt aus dem Token (nicht aus einem Pfad-Parameter, also keine
Cross-Agent-Gefahr), der Eval-Gate wird respektiert (derselbe Sicherheitsnetz
wie beim Admin-Update), und Fehlerpfade (404/500) werden sauber durchgereicht.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.agent_apps import agent_restart_self


def _agent():
    return SimpleNamespace(id="agent1", container_id="c123")


class RestartOwnContainerTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_id_comes_from_the_token_not_a_parameter(self):
        """Kein Pfad-Parameter fuer agent_id — der Aufrufer kann nur sich
        selbst neu starten, niemals einen fremden Agenten."""
        db = SimpleNamespace()
        docker = SimpleNamespace()
        redis = SimpleNamespace()
        manager = AsyncMock()
        manager.update_agent = AsyncMock(return_value=_agent())

        with patch("app.api.agent_apps._load_agent", AsyncMock(return_value=_agent())), \
             patch("app.services.eval_service.gate_for_agent", AsyncMock(return_value={"allowed": True})), \
             patch("app.api.agent_apps.AgentManager", return_value=manager):
            result = await agent_restart_self(
                auth={"agent_id": "agent1"}, db=db, docker=docker, redis=redis,
            )

        self.assertEqual(result, {"status": "restarted", "agent_id": "agent1"})
        manager.update_agent.assert_awaited_once_with("agent1")

    async def test_eval_gate_blocks_a_disallowed_restart(self):
        db = SimpleNamespace()
        docker = SimpleNamespace()
        redis = SimpleNamespace()
        manager = AsyncMock()

        with patch("app.api.agent_apps._load_agent", AsyncMock(return_value=_agent())), \
             patch("app.services.eval_service.gate_for_agent",
                   AsyncMock(return_value={"allowed": False, "message": "evals failing"})), \
             patch("app.api.agent_apps.AgentManager", return_value=manager):
            with self.assertRaises(HTTPException) as ctx:
                await agent_restart_self(auth={"agent_id": "agent1"}, db=db, docker=docker, redis=redis)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["error"], "eval_gate")
        manager.update_agent.assert_not_called()

    async def test_missing_agent_is_404(self):
        db = SimpleNamespace()
        docker = SimpleNamespace()
        redis = SimpleNamespace()
        manager = AsyncMock()
        manager.update_agent = AsyncMock(side_effect=ValueError("not found"))

        with patch("app.api.agent_apps._load_agent", AsyncMock(return_value=_agent())), \
             patch("app.services.eval_service.gate_for_agent", AsyncMock(return_value={"allowed": True})), \
             patch("app.api.agent_apps.AgentManager", return_value=manager):
            with self.assertRaises(HTTPException) as ctx:
                await agent_restart_self(auth={"agent_id": "agent1"}, db=db, docker=docker, redis=redis)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_docker_failure_is_a_500_not_a_silent_swallow(self):
        db = SimpleNamespace()
        docker = SimpleNamespace()
        redis = SimpleNamespace()
        manager = AsyncMock()
        manager.update_agent = AsyncMock(side_effect=RuntimeError("docker daemon unreachable"))

        with patch("app.api.agent_apps._load_agent", AsyncMock(return_value=_agent())), \
             patch("app.services.eval_service.gate_for_agent", AsyncMock(return_value={"allowed": True})), \
             patch("app.api.agent_apps.AgentManager", return_value=manager):
            with self.assertRaises(HTTPException) as ctx:
                await agent_restart_self(auth={"agent_id": "agent1"}, db=db, docker=docker, redis=redis)

        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
