"""Regression tests for Sentinel epic #588, sub-issue #589 (Redis ACL separation).

Today every agent container connects to Redis with the ONE shared admin
credential (settings.redis_url_internal) — any agent can PUBLISH/LPUSH/HSET
into any OTHER agent's key/channel space (e.g. spoof agent:{other_id}:logs),
and can run admin commands (FLUSHALL, CONFIG, ACL, KEYS, ...) against the
shared instance. These tests pin down the intended least-privilege ACL rule
set (build_agent_acl_setuser_args) and the agent_manager.py wiring that uses
it — without needing a live Redis. Feature is behind settings.redis_acl_enabled
(default off, see config.py) until manually smoke-tested against a real
redis-server ACL.
"""
import pytest

from app.services.redis_service import (
    RedisService,
    agent_acl_password,
    agent_acl_username,
    build_agent_acl_setuser_args,
)


def test_agent_acl_username_is_stable_and_agent_scoped():
    assert agent_acl_username("abc123") == "agent-abc123"
    assert agent_acl_username("abc123") != agent_acl_username("def456")


def test_agent_acl_password_is_deterministic_and_agent_scoped():
    # Deterministic: no extra secret to persist/rotate, re-derivable from
    # agent_id + api_secret_key alone (same pattern as make_agent_token()).
    assert agent_acl_password("abc123") == agent_acl_password("abc123")
    assert agent_acl_password("abc123") != agent_acl_password("def456")


def test_agent_acl_password_domain_separated_from_agent_auth_token():
    # Must not be derivable from / collide with the HMAC agent auth token
    # (make_agent_token in dependencies.py) even though both are HMAC-SHA256
    # over api_secret_key + agent_id — the "redis-acl:" prefix guarantees a
    # different HMAC input.
    from app.dependencies import make_agent_token
    assert agent_acl_password("abc123") != make_agent_token("abc123")


def test_build_agent_acl_setuser_args_scopes_keys_to_own_agent_and_meeting_response():
    args = build_agent_acl_setuser_args("abc123")
    key_patterns = [a[1:] for a in args if a.startswith("~")]
    assert "agent:abc123:*" in key_patterns
    # meeting:*:response:{id} is still "own key space" despite the wildcard:
    # only room_id varies, {id} is always this agent's own id.
    assert "meeting:*:response:abc123" in key_patterns
    # The broad agent:*:messages wildcard must be GONE from the general read/write
    # key patterns (review finding: it let any agent LRANGE/LREM/LPOP another
    # agent's inbox) — cross-agent write-only access now comes from the
    # dedicated LPUSH-only selector, checked separately below.
    assert "agent:*:messages" not in key_patterns
    # Must NOT get a blanket "any key" pattern.
    assert "*" not in key_patterns


def test_build_agent_acl_setuser_args_grants_lpush_only_selector_for_cross_agent_inbox():
    args = build_agent_acl_setuser_args("abc123")
    assert "(~agent:*:messages +lpush)" in args


def test_build_agent_acl_setuser_args_scopes_channels_to_own_agent_and_globals():
    args = build_agent_acl_setuser_args("abc123")
    channel_patterns = [a[1:] for a in args if a.startswith("&")]
    assert "agent:abc123:*" in channel_patterns
    for global_channel in (
        "agents:logs:all",
        "chat:completions",
        "agent:messages:persist",
        "task:started",
        "task:completions",
    ):
        assert global_channel in channel_patterns
    assert "*" not in channel_patterns


def test_build_agent_acl_setuser_args_allows_the_connection_basics():
    """Ohne @connection fehlt PING — und darauf stuetzen sich Verbindungsaufbau
    und Gesundheitspruefung von redis-py. Der Agent kam ohne diese Kategorie gar
    nicht erst hoch; gefunden vom Rauchtest gegen ein echtes Redis 7.4
    (test_redis_acl_live_smoke.py), unsichtbar fuer reine Modultests."""
    args = build_agent_acl_setuser_args("a1")
    assert "+@connection" in args


def test_the_connection_grant_comes_before_the_denials():
    """Reihenfolge ist hier Semantik: @connection enthaelt auch CLIENT LIST, das
    erst durch die nachfolgenden -@admin/-@dangerous wieder entzogen wird. Stuende
    es danach, waere CLIENT LIST offen."""
    args = build_agent_acl_setuser_args("a1")
    assert args.index("+@connection") < args.index("-@admin")
    assert args.index("+@connection") < args.index("-@dangerous")


def test_build_agent_acl_setuser_args_denies_admin_and_dangerous_categories():
    args = build_agent_acl_setuser_args("abc123")
    assert "-@admin" in args
    assert "-@dangerous" in args
    assert "+@admin" not in args


def test_build_agent_acl_setuser_args_does_not_leak_another_agents_pattern():
    args_a = build_agent_acl_setuser_args("agent-a")
    args_b = build_agent_acl_setuser_args("agent-b")
    assert "agent:agent-b:*" not in [a[1:] for a in args_a if a.startswith(("~", "&"))]
    assert "agent:agent-a:*" not in [a[1:] for a in args_b if a.startswith(("~", "&"))]


class _FakeAclClient:
    """Records ACL SETUSER/DELUSER calls instead of hitting a live Redis."""

    def __init__(self):
        self.calls = []

    async def execute_command(self, *args):
        self.calls.append(args)
        if args[:2] == ("ACL", "DELUSER") and getattr(self, "raise_on_deluser", False):
            raise RuntimeError("simulated: unknown user")
        return "OK"


@pytest.mark.asyncio
async def test_ensure_agent_acl_user_issues_setuser_and_returns_scoped_url():
    svc = RedisService("redis://:adminpw@ai-employee-redis:6379")
    svc.client = _FakeAclClient()

    url = await svc.ensure_agent_acl_user("abc123")

    assert svc.client.calls[0][0:2] == ("ACL", "SETUSER")
    assert svc.client.calls[0][2] == "agent-abc123"
    # Scoped URL uses the agent's own username/password, not the admin one,
    # but keeps the same host/port so it still reaches the same Redis.
    assert url.startswith("redis://agent-abc123:")
    assert "adminpw" not in url
    assert "ai-employee-redis:6379" in url


@pytest.mark.asyncio
async def test_ensure_agent_acl_user_requires_connected_client():
    svc = RedisService("redis://ai-employee-redis:6379")
    with pytest.raises(RuntimeError):
        await svc.ensure_agent_acl_user("abc123")


@pytest.mark.asyncio
async def test_revoke_agent_acl_user_issues_deluser():
    svc = RedisService("redis://:adminpw@ai-employee-redis:6379")
    svc.client = _FakeAclClient()

    await svc.revoke_agent_acl_user("abc123")

    assert svc.client.calls == [("ACL", "DELUSER", "agent-abc123")]


@pytest.mark.asyncio
async def test_revoke_agent_acl_user_swallows_errors():
    # Deleting an already-gone agent's ACL user (e.g. double-cleanup) must
    # not blow up remove_agent() — this is best-effort housekeeping.
    svc = RedisService("redis://:adminpw@ai-employee-redis:6379")
    client = _FakeAclClient()
    client.raise_on_deluser = True
    svc.client = client

    await svc.revoke_agent_acl_user("abc123")  # must not raise


@pytest.mark.asyncio
async def test_ensure_agent_acl_user_raises_under_sentinel_ha(monkeypatch):
    # self.redis_url is a fixed standalone address; under REDIS_SENTINEL_URL the
    # real master is discovered dynamically and can move on failover, so a scoped
    # URL baked from self.redis_url would point an agent at a stale/wrong node.
    # Must raise (fail-closed), not silently return a wrong URL.
    monkeypatch.setenv("REDIS_SENTINEL_URL", "sentinel://ai-employee-sentinel:26379")
    svc = RedisService("redis://:adminpw@ai-employee-redis:6379")
    svc.client = _FakeAclClient()

    with pytest.raises(NotImplementedError):
        await svc.ensure_agent_acl_user("abc123")
