"""Unit tests for TelegramBotManager token-dedup logic (issue #318, follow-up to #317).

A Telegram bot token may only be polled by a single getUpdates loop. The manager
must never start a second poller for a token that another agent's bot already
claims — otherwise Telegram raises "terminated by other getUpdates request" and
every reply is delivered twice.

Issue #709: the manager itself no longer knows about a "global" token — that
distinction moved to ``main.py``'s startup order (per-agent bots load first via
``load_all_from_db``, the global controller bot starts afterwards only if
``is_token_claimed()`` says its token is still free). A per-agent bot whose
token happens to equal the global bot's token now starts normally; it is the
GLOBAL bot that yields, not the agent.

These tests mock TelegramAgentBot so no network calls happen.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

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
def _patch_bot(monkeypatch):
    """Replace the real bot with a network-free fake."""
    import app.telegram.bot_manager as bm_mod

    monkeypatch.setattr(bm_mod, "TelegramAgentBot", _FakeBot)


@pytest.mark.asyncio
async def test_an_agent_on_the_global_bots_token_starts_normally():
    """Issue #709: the per-agent bot takes priority now — it is the global bot
    that must yield in main.py, not the agent. bot_manager itself has no
    concept of a "global" token to skip; an agent claiming that same token
    string is just an ordinary agent to it."""
    mgr = TelegramBotManager()
    await mgr.load_all_from_db(_db([_Agent("a1", "Agent1", "GLOBAL")]))
    assert "a1" in mgr._bots
    assert mgr._bots["a1"].bot_token == "GLOBAL"


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


# `is_token_claimed()` is what main.py asks before starting the global bot
# (issue #709) — it must see a per-agent bot's token as claimed, regardless of
# which caller (agent dedup vs. global startup) is asking.


@pytest.mark.asyncio
async def test_an_active_agent_bots_token_is_claimed():
    mgr = TelegramBotManager()
    await mgr.start_bot("a1", "Agent1", "T1", "AUTH")
    assert mgr.is_token_claimed("T1") is True


@pytest.mark.asyncio
async def test_an_unused_token_is_not_claimed():
    mgr = TelegramBotManager()
    await mgr.start_bot("a1", "Agent1", "T1", "AUTH")
    assert mgr.is_token_claimed("T2") is False


@pytest.mark.asyncio
async def test_ignore_agent_id_excludes_its_own_bot():
    """start_bot's own dedup check must not see itself as a collision."""
    mgr = TelegramBotManager()
    await mgr.start_bot("a1", "Agent1", "T1", "AUTH")
    assert mgr.is_token_claimed("T1", ignore_agent_id="a1") is False
    assert mgr.is_token_claimed("T1", ignore_agent_id="a2") is True


def test_an_empty_manager_claims_nothing():
    mgr = TelegramBotManager()
    assert mgr.is_token_claimed("ANY") is False


def test_main_starts_per_agent_bots_before_the_global_one():
    """Issue #709: per-agent bots must load BEFORE the global bot decides
    whether to start, so ``is_token_claimed()`` sees a real answer. Checked by
    ORDER of the two calls, not a character-distance window (#726) — a
    harmless line inserted between them must not turn this test red."""
    import re

    quelle = (
        (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py")
        .read_text()
    )
    laden = re.search(r"await tg_manager\.load_all_from_db\(", quelle)
    start_global = re.search(r"telegram_task = asyncio\.create_task\(bot\.start\(\)\)", quelle)
    assert laden and start_global, "beide Aufrufe muessen in main.py vorkommen"
    assert laden.start() < start_global.start(), (
        "per-Agent-Bots muessen laden, BEVOR der globale Bot startet — sonst "
        "sieht is_token_claimed() den falschen Zustand"
    )
