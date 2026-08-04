"""Tests for the chat-websocket skill auto-injection guard (issue #472, PR #471 W1).

Covers ``_auto_inject_skills_for_chat`` in app/api/ws.py — the seam that mirrors
the task path (task_router.py) so a chat-only agent also picks up path/role-matched
skills (#468). It must inject exactly once per NEW session and never let an
injection failure break the chat turn.
"""
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import ws


def _patch_session_factory():
    """Patch async_session_factory so ``async with ... as skill_db`` yields a mock."""
    skill_db = AsyncMock()

    @asynccontextmanager
    async def _factory():
        yield skill_db

    return patch.object(ws, "async_session_factory", _factory), skill_db


class AutoInjectSkillsForChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_injects_once(self):
        inject = AsyncMock(return_value=[{"skill_id": 1, "skill_name": "x", "assigned_by": "auto:path"}])
        sess_patch, _ = _patch_session_factory()
        with patch("app.services.skill_auto_injector.auto_inject_skills", inject), sess_patch:
            await ws._auto_inject_skills_for_chat("agent-1", "Fix /app/models/user.py", True, "sess123")
        inject.assert_awaited_once()
        # agent_id and prompt text are forwarded to the injector
        args = inject.await_args.args
        self.assertEqual(args[1], "agent-1")
        self.assertEqual(args[2], "Fix /app/models/user.py")

    async def test_resumed_session_does_not_reinject(self):
        inject = AsyncMock()
        sess_patch, _ = _patch_session_factory()
        with patch("app.services.skill_auto_injector.auto_inject_skills", inject), sess_patch:
            await ws._auto_inject_skills_for_chat("agent-1", "hello again", False, "sess123")
        inject.assert_not_awaited()

    async def test_empty_text_does_not_inject(self):
        inject = AsyncMock()
        sess_patch, _ = _patch_session_factory()
        with patch("app.services.skill_auto_injector.auto_inject_skills", inject), sess_patch:
            await ws._auto_inject_skills_for_chat("agent-1", "", True, "sess123")
        inject.assert_not_awaited()

    async def test_injection_exception_is_swallowed_and_logged(self):
        inject = AsyncMock(side_effect=RuntimeError("db down"))
        sess_patch, _ = _patch_session_factory()
        with patch("app.services.skill_auto_injector.auto_inject_skills", inject), sess_patch, \
                patch.object(ws.logger, "warning") as warn:
            # Must NOT raise — a failed injection may never break the chat turn.
            await ws._auto_inject_skills_for_chat("agent-1", "Fix /app/x.py", True, "sess123")
        inject.assert_awaited_once()
        warn.assert_called_once()
        self.assertIn("sess123", warn.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
