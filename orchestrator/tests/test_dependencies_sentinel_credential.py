"""Tests for the Sentinel-exclusive credential scheme (issue #590 scope point 3).

Pins the properties the design in #590/#588 depends on:
  - deterministic across calls/restarts (derived from api_secret_key, not random)
  - a valid Sentinel token is accepted; missing/empty/wrong tokens are rejected
  - it lives in its own HMAC domain, separate from per-agent tokens (make_agent_token) —
    an agent's own token must never satisfy require_sentinel, and vice versa
  - SentinelPrincipal is distinguishable from AgentPrincipal via principal_type/role,
    same shape as the existing is_agent_principal() check
"""

import unittest
from types import SimpleNamespace

import pytest

from app.dependencies import (
    SentinelPrincipal,
    get_sentinel_token,
    is_sentinel_principal,
    make_agent_token,
    require_sentinel,
)
from fastapi import HTTPException


def _request(auth_header: str | None) -> SimpleNamespace:
    """Minimal stand-in for a Starlette Request — require_sentinel only reads .headers."""
    headers = {"Authorization": auth_header} if auth_header else {}
    return SimpleNamespace(headers=headers)


class TestGetSentinelToken(unittest.TestCase):
    def test_deterministic_across_calls(self):
        self.assertEqual(get_sentinel_token(), get_sentinel_token())

    def test_distinct_from_an_agent_token(self):
        # Different HMAC domain (fixed constant vs. agent_id) must never collide.
        self.assertNotEqual(get_sentinel_token(), make_agent_token("some-agent-id"))

    def test_is_a_non_trivial_hex_digest(self):
        token = get_sentinel_token()
        self.assertEqual(len(token), 64)  # full SHA-256 hex digest
        int(token, 16)  # raises ValueError if not hex


class TestRequireSentinel(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_valid_token(self):
        request = _request(f"Bearer {get_sentinel_token()}")
        principal = await require_sentinel(request)
        self.assertIsInstance(principal, SentinelPrincipal)
        self.assertTrue(is_sentinel_principal(principal))

    async def test_rejects_missing_authorization_header(self):
        request = _request(None)
        with pytest.raises(HTTPException) as exc_info:
            await require_sentinel(request)
        self.assertEqual(exc_info.value.status_code, 403)

    async def test_rejects_empty_token(self):
        request = _request("Bearer ")
        with pytest.raises(HTTPException) as exc_info:
            await require_sentinel(request)
        self.assertEqual(exc_info.value.status_code, 403)

    async def test_rejects_wrong_token(self):
        request = _request("Bearer not-the-sentinel-token")
        with pytest.raises(HTTPException):
            await require_sentinel(request)

    async def test_rejects_an_agent_token_presented_as_sentinel_credential(self):
        """The actor Sentinel may need to stop must never be able to impersonate it."""
        agent_token = make_agent_token("agent-1")
        request = _request(f"Bearer {agent_token}")
        with pytest.raises(HTTPException):
            await require_sentinel(request)


class TestIsSentinelPrincipal(unittest.TestCase):
    def test_true_for_sentinel_principal(self):
        self.assertTrue(is_sentinel_principal(SentinelPrincipal()))

    def test_false_for_plain_namespace(self):
        self.assertFalse(is_sentinel_principal(SimpleNamespace(role="agent")))

    def test_false_for_none(self):
        self.assertFalse(is_sentinel_principal(None))


if __name__ == "__main__":
    unittest.main()
