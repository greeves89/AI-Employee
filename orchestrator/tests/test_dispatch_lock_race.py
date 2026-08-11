"""Race-simulation tests for RedisService's atomic per-agent dispatch lock (#548).

The bug: two schedules due for the SAME agent in the same/overlapping scheduler
ticks could both read "queue empty, agent idle" and both push a task, so the
agent ran two unrelated jobs concurrently against the same shared /workspace
checkout (confirmed 3rd+ occurrence, see issue #548). This tests the lock
primitive in isolation (no live Redis needed) with a minimal fake client that
reproduces the semantics that matter: SET NX for mutual exclusion, and a
Lua-script compare-and-delete for release so a stale/expired holder can't
release a lock someone else has since acquired.
"""

import asyncio
import unittest

from app.services.redis_service import RedisService


class _FakeAsyncRedis:
    """Minimal fake reproducing just the SET NX / Lua-eval semantics we use."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def set(self, key, value, nx=False, ex=None):
        async with self._lock:
            if nx and key in self._store:
                return None
            self._store[key] = value
            return True

    async def get(self, key):
        return self._store.get(key)

    async def eval(self, script, numkeys, key, arg):
        # Only the exact CAS-release script used by RedisService is supported.
        async with self._lock:
            if self._store.get(key) == arg:
                del self._store[key]
                return 1
            return 0


def _service_with_fake_client() -> tuple[RedisService, _FakeAsyncRedis]:
    svc = RedisService(redis_url="redis://fake")
    fake = _FakeAsyncRedis()
    svc.client = fake
    return svc, fake


class DispatchLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_concurrent_acquire_is_rejected(self):
        """Two 'simultaneous' dispatchers for the same agent must not both win."""
        svc, _ = _service_with_fake_client()
        token_a = await svc.acquire_dispatch_lock("agent-1")
        token_b = await svc.acquire_dispatch_lock("agent-1")
        self.assertIsNotNone(token_a)
        self.assertIsNone(token_b, "a second dispatcher must NOT acquire the lock")

    async def test_lock_is_per_agent(self):
        """A lock held for one agent must not block dispatch for another."""
        svc, _ = _service_with_fake_client()
        token_a = await svc.acquire_dispatch_lock("agent-1")
        token_other = await svc.acquire_dispatch_lock("agent-2")
        self.assertIsNotNone(token_a)
        self.assertIsNotNone(token_other)

    async def test_release_then_reacquire_succeeds(self):
        """After the holder releases, the next dispatcher must be able to proceed
        (this is what lets a schedule fire on the very next tick once the agent
        frees up, instead of getting stuck)."""
        svc, _ = _service_with_fake_client()
        token_a = await svc.acquire_dispatch_lock("agent-1")
        await svc.release_dispatch_lock("agent-1", token_a)
        token_b = await svc.acquire_dispatch_lock("agent-1")
        self.assertIsNotNone(token_b, "lock must be re-acquirable after release")

    async def test_release_with_wrong_token_is_a_noop(self):
        """A stale/expired holder (e.g. after its TTL lapsed and someone else
        already grabbed the lock) must NOT be able to delete the new holder's
        lock — that would reopen the exact race this fix closes."""
        svc, fake = _service_with_fake_client()
        token_a = await svc.acquire_dispatch_lock("agent-1")
        self.assertIsNotNone(token_a)
        # Simulate: token_a's dispatcher is delayed and tries to release with a
        # token that no longer matches the current holder (e.g. because the key
        # expired and someone else re-acquired it in the meantime).
        await svc.release_dispatch_lock("agent-1", "some-other-stale-token")
        # The real lock must still be held (by token_a) — a third dispatcher
        # must still be rejected.
        token_c = await svc.acquire_dispatch_lock("agent-1")
        self.assertIsNone(token_c, "a mismatched token must not release someone else's lock")

    async def test_concurrent_acquire_race_only_one_winner(self):
        """Fire N concurrent acquire attempts for the same agent (simulating
        overlapping scheduler ticks); exactly one must win."""
        svc, _ = _service_with_fake_client()
        results = await asyncio.gather(
            *(svc.acquire_dispatch_lock("agent-race") for _ in range(10))
        )
        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1, "exactly one of N racing dispatchers must win the lock")

    async def test_no_client_is_safe_noop(self):
        """Without a live Redis connection, acquiring must fail closed (None,
        never a false 'lock acquired') and releasing must not raise."""
        svc = RedisService(redis_url="redis://fake")
        svc.client = None
        token = await svc.acquire_dispatch_lock("agent-1")
        self.assertIsNone(token)
        await svc.release_dispatch_lock("agent-1", "whatever")  # must not raise


if __name__ == "__main__":
    unittest.main()
