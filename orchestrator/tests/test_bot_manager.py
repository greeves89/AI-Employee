"""Unit tests for TelegramBotManager token-dedup logic (issue #318, follow-up to #317).

A Telegram bot token may only be polled by a single getUpdates loop. The manager
must never start a second poller for a token that is already claimed — by the
global notification bot or by another agent's bot — otherwise Telegram raises
"terminated by other getUpdates request" and every reply is delivered twice.

These tests mock TelegramAgentBot so no network calls happen.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.telegram import bot_manager as bm_mod
from app.telegram.bot_manager import TelegramBotManager


class _FakeBot:
    """Stand-in for TelegramAgentBot — records its token, no network."""

    def __init__(self, agent_id, agent_name, bot_token, auth_key):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.bot_token = bot_token
        self.auth_key = auth_key
        self._started = False

    async def start(self):
        self._started = True

    async def stop(self):
        self._started = False


class _Agent:
    def __init__(self, agent_id, name, token):
        self.id = agent_id
        self.name = name
        self.config = {"telegram_bot_token": token, "telegram_auth_key": "AUTH"}


def _db(agents):
    """AsyncSession stub whose execute(...).scalars().all() returns *agents*."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = agents
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture(autouse=True)
def _patch_bot_and_global_token(monkeypatch):
    """Replace the real bot with a network-free fake and pin the global token."""
    monkeypatch.setattr(bm_mod, "TelegramAgentBot", _FakeBot)
    monkeypatch.setattr(bm_mod.settings, "telegram_bot_token", "GLOBAL", raising=False)


@pytest.mark.asyncio
async def test_agent_token_equal_global_token_is_skipped():
    """Case 1: an agent whose token IS the global bot token is never started."""
    mgr = TelegramBotManager()
    await mgr.load_all_from_db(_db([_Agent("a1", "Agent1", "GLOBAL")]))
    assert "a1" not in mgr._bots


@pytest.mark.asyncio
async def test_two_agents_same_token_only_first_starts():
    """Case 2: two agents sharing a non-global token → only the first polls it."""
    mgr = TelegramBotManager()
    await mgr.load_all_from_db(
        _db([_Agent("a1", "Agent1", "T1"), _Agent("a2", "Agent2", "T1")])
    )
    assert "a1" in mgr._bots
    assert "a2" not in mgr._bots


@pytest.mark.asyncio
async def test_two_agents_distinct_tokens_both_start():
    """Case 3: distinct unique tokens → both bots start (behavior preserved)."""
    mgr = TelegramBotManager()
    await mgr.load_all_from_db(
        _db([_Agent("a1", "Agent1", "T1"), _Agent("a2", "Agent2", "T2")])
    )
    assert "a1" in mgr._bots
    assert "a2" in mgr._bots
    assert mgr._bots["a1"].bot_token == "T1"
    assert mgr._bots["a2"].bot_token == "T2"


@pytest.mark.asyncio
async def test_start_bot_runtime_guard_rejects_already_claimed_token():
    """Case 4: start_bot for a token a running bot already polls returns early."""
    mgr = TelegramBotManager()
    await mgr.start_bot("a1", "Agent1", "T1", "AUTH")
    assert "a1" in mgr._bots

    # Second agent tries the same token at runtime → refused, no second poller.
    await mgr.start_bot("a2", "Agent2", "T1", "AUTH")
    assert "a2" not in mgr._bots
    assert mgr._bots["a1"].bot_token == "T1"
