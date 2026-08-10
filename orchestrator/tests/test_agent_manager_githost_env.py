"""Regression test: AgentManager._get_integration_env routes GitHub token
injection through the GitHostProvider interface (#532 phase 1), preserving
the GITHUB_TOKEN/GH_TOKEN env vars agents received before the refactor.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.agent_manager import AgentManager


def _db_with_integration(integration):
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = integration
    db.execute = AsyncMock(return_value=exec_result)
    return db


@pytest.mark.asyncio
async def test_github_integration_env_via_provider():
    integration = MagicMock()
    integration.access_token_encrypted = "encrypted-blob"
    db = _db_with_integration(integration)
    manager = AgentManager(db=db, docker=MagicMock(), redis=MagicMock())

    with patch("app.core.agent_manager.decrypt_token", return_value="ghp_decrypted"):
        env = await manager._get_integration_env(["github"])

    assert env == {"GITHUB_TOKEN": "ghp_decrypted", "GH_TOKEN": "ghp_decrypted"}


@pytest.mark.asyncio
async def test_no_github_integration_row_yields_empty_env():
    db = _db_with_integration(None)
    manager = AgentManager(db=db, docker=MagicMock(), redis=MagicMock())

    env = await manager._get_integration_env(["github"])

    assert env == {}
