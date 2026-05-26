import pytest

from app.tools import executor


@pytest.mark.anyio
async def test_command_policy_returns_first_match(monkeypatch):
    async def fake_policies():
        return [
            {"name": "git force", "pattern": r"git\s+push\s+--force", "effect": "medium"},
            {"name": "allow git", "pattern": r"git", "effect": "allow"},
        ]

    monkeypatch.setattr(executor, "_get_command_policies", fake_policies)

    effect, reason = await executor._evaluate_command_policy("git push --force origin main")

    assert effect == "medium"
    assert reason == "git force"


@pytest.mark.anyio
async def test_command_policy_invalid_regex_is_ignored(monkeypatch):
    async def fake_policies():
        return [
            {"name": "broken", "pattern": r"[", "effect": "blocked"},
            {"name": "safe override", "pattern": r"echo", "effect": "allow"},
        ]

    monkeypatch.setattr(executor, "_get_command_policies", fake_policies)

    effect, reason = await executor._evaluate_command_policy("echo ok")

    assert effect == "allow"
    assert reason == "safe override"
