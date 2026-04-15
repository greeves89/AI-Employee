"""
RLS Regression + Smoke Test Suite
==================================

Validates the fix from commit fba0998 (formerly 7183cc4):
"db.commit() in auth middleware destroyed RLS settings, hiding all agents"

Background:
    get_current_user() updates users.last_active_at once per minute.
    That UPDATE was committed on the request session — which destroys
    the SET LOCAL RLS bypass ("app.bypass_rls = yes") that was applied
    earlier in the same request. After the commit, the endpoint's own
    query ran without bypass and the RLS policy filtered out every
    agent row, returning an empty list to the user.

The fix moves the activity update into a SEPARATE short-lived session
so the request session's transaction stays alive and SET LOCAL persists.

This test proves the fix by:
  1. Listing agents (fresh session, should show agents)
  2. Forcing last_active_at to "2 minutes ago" in the DB
  3. Listing agents again — which will trigger the activity update
     path AND test that subsequent queries still see the agents.
  4. If the fix is broken, call #3 returns an empty list.

Plus general smoke tests + a concurrent-request test (validates the
asyncio.gather → sequential loop fix in api/agents.py).

Run with:
    python3 scripts/test_rls_regression.py
"""
import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

BASE_URL = "http://localhost:8000"


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0

    def render(self) -> str:
        icon = "✅" if self.passed else "❌"
        ms = f"({self.duration_ms:.0f}ms)"
        return f"  {icon} {self.name:<55s} {ms}\n     {self.detail}" if self.detail else f"  {icon} {self.name:<55s} {ms}"


class TestSuite:
    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self.token: str | None = None

    async def mint_token(self) -> str:
        """Mint an admin JWT via docker exec into the orchestrator."""
        import subprocess
        cmd = [
            "docker", "exec", "ai-employee-orchestrator", "python", "-c",
            """
from app.core.auth import create_access_token
from app.db.session import async_session_factory
from app.models.user import User, UserRole
from sqlalchemy import select
import asyncio

async def mint():
    async with async_session_factory() as db:
        user = await db.scalar(select(User).where(User.role == UserRole.ADMIN))
        if not user:
            print('ERROR')
            return
        print(create_access_token(user.id, user.role))

asyncio.run(mint())
            """.strip(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        token = result.stdout.strip()
        if not token or token == "ERROR":
            raise RuntimeError(f"Could not mint token: {result.stderr}")
        return token

    async def _record(self, name: str, fn) -> TestResult:
        start = time.time()
        try:
            detail = await fn()
            ms = (time.time() - start) * 1000
            r = TestResult(name, True, detail or "", ms)
        except AssertionError as e:
            ms = (time.time() - start) * 1000
            r = TestResult(name, False, str(e), ms)
        except Exception as e:
            ms = (time.time() - start) * 1000
            r = TestResult(name, False, f"{type(e).__name__}: {e}", ms)
        self.results.append(r)
        print(r.render(), flush=True)
        return r

    async def _authed_session(self) -> aiohttp.ClientSession:
        jar = aiohttp.CookieJar(unsafe=True)
        session = aiohttp.ClientSession(cookie_jar=jar)
        session.cookie_jar.update_cookies({"access_token": self.token})
        return session

    # ---------- TESTS ----------

    async def test_health(self) -> str:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BASE_URL}/api/v1/version/") as r:
                assert r.status == 200, f"expected 200, got {r.status}"
                data = await r.json()
                return f"version={data.get('version', '?')}"

    async def test_unauth_blocks_agents(self) -> str:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BASE_URL}/api/v1/agents/") as r:
                assert r.status == 401, f"expected 401 w/o auth, got {r.status}"
                return "401 Unauthorized as expected"

    async def test_auth_agents_basic(self) -> str:
        async with await self._authed_session() as s:
            async with s.get(f"{BASE_URL}/api/v1/agents/") as r:
                assert r.status == 200, f"expected 200, got {r.status}"
                data = await r.json()
                agents = data.get("agents", [])
                assert len(agents) > 0, "no agents returned (fix missing?)"
                return f"{len(agents)} agents visible"

    async def test_rls_regression_after_forced_activity_update(self) -> str:
        """
        THIS is the actual regression test for the fix.
        1. Force user.last_active_at to 2 minutes ago
        2. Call /agents/ which will trigger the activity-update path
        3. Verify agents are STILL visible (would be empty without fix)
        """
        import subprocess

        # Step 1: force last_active_at to 2 min ago for all admin users
        force_cmd = [
            "docker", "exec", "ai-employee-postgres", "psql", "-U", "ai_employee",
            "-d", "ai_employee", "-c",
            "UPDATE users SET last_active_at = NOW() - INTERVAL '2 minutes' WHERE role = 'ADMIN';",
        ]
        r = subprocess.run(force_cmd, capture_output=True, text=True, timeout=10)
        assert "UPDATE" in r.stdout, f"could not force last_active_at: {r.stderr}"

        # Step 2: call /agents/ — will trigger activity update in get_current_user
        async with await self._authed_session() as s:
            async with s.get(f"{BASE_URL}/api/v1/agents/") as resp:
                assert resp.status == 200, f"expected 200, got {resp.status}"
                data = await resp.json()
                agents = data.get("agents", [])

        # Step 3: verify agents are still visible (the fix works)
        assert len(agents) > 0, (
            "❗ RLS BUG IS BACK: agents list is empty after forced activity update. "
            "db.commit() in auth middleware is destroying SET LOCAL RLS again."
        )
        return f"{len(agents)} agents still visible after forced activity update"

    async def test_smoke_tasks(self) -> str:
        async with await self._authed_session() as s:
            async with s.get(f"{BASE_URL}/api/v1/tasks/") as r:
                assert r.status == 200, f"expected 200, got {r.status}"
                data = await r.json()
                count = len(data) if isinstance(data, list) else data.get("total", 0)
                return f"{count} tasks"

    async def test_smoke_schedules(self) -> str:
        async with await self._authed_session() as s:
            async with s.get(f"{BASE_URL}/api/v1/schedules/") as r:
                assert r.status == 200, f"expected 200, got {r.status}"
                return "ok"

    async def test_smoke_notifications(self) -> str:
        async with await self._authed_session() as s:
            async with s.get(f"{BASE_URL}/api/v1/notifications/count") as r:
                assert r.status == 200, f"expected 200, got {r.status}"
                data = await r.json()
                return f"unread={data.get('count', 0)}"

    async def test_smoke_settings(self) -> str:
        async with await self._authed_session() as s:
            async with s.get(f"{BASE_URL}/api/v1/settings/") as r:
                assert r.status == 200, f"expected 200, got {r.status}"
                return "ok"

    async def test_concurrent_agent_list(self) -> str:
        """
        Fire 10 concurrent /agents/ requests. This validates the
        asyncio.gather → sequential loop fix in api/agents.py — before
        the fix, concurrent requests would crash with asyncpg
        "another operation is in progress".
        """
        async def one_call() -> int:
            async with await self._authed_session() as s:
                async with s.get(f"{BASE_URL}/api/v1/agents/") as r:
                    if r.status != 200:
                        return -r.status
                    data = await r.json()
                    return len(data.get("agents", []))

        results = await asyncio.gather(*(one_call() for _ in range(10)), return_exceptions=True)
        failures = [r for r in results if isinstance(r, Exception) or (isinstance(r, int) and r < 0)]
        assert not failures, f"{len(failures)}/10 concurrent calls failed: {failures[:3]}"

        counts = [r for r in results if isinstance(r, int) and r >= 0]
        assert all(c == counts[0] for c in counts), f"inconsistent agent counts: {counts}"
        return f"10/10 concurrent calls succeeded, each returned {counts[0]} agents"

    async def test_repeated_activity_update_stable(self) -> str:
        """Repeatedly force activity update + list agents — must never return empty."""
        import subprocess

        for i in range(3):
            subprocess.run(
                ["docker", "exec", "ai-employee-postgres", "psql", "-U", "ai_employee",
                 "-d", "ai_employee", "-c",
                 "UPDATE users SET last_active_at = NOW() - INTERVAL '5 minutes' WHERE role = 'ADMIN';"],
                capture_output=True, text=True, timeout=10,
            )
            async with await self._authed_session() as s:
                async with s.get(f"{BASE_URL}/api/v1/agents/") as r:
                    assert r.status == 200, f"iteration {i}: got {r.status}"
                    data = await r.json()
                    agents = data.get("agents", [])
                    assert len(agents) > 0, f"iteration {i}: empty agent list"

        return "3/3 iterations stable — fix is solid"

    # ---------- RUNNER ----------

    async def run_all(self) -> None:
        print("\n" + "=" * 70)
        print("  RLS REGRESSION + SMOKE TEST SUITE")
        print("  Target:", BASE_URL)
        print("=" * 70)

        print("\n[setup] Minting admin JWT via docker exec ...")
        try:
            self.token = await self.mint_token()
            print(f"[setup] Token minted: {self.token[:40]}...")
        except Exception as e:
            print(f"[setup] FAILED to mint token: {e}")
            sys.exit(1)

        print("\n── Smoke Tests ──────────────────────────────────────────────────")
        await self._record("health endpoint /version/", self.test_health)
        await self._record("unauthenticated /agents/ returns 401", self.test_unauth_blocks_agents)
        await self._record("authed /agents/ returns agents", self.test_auth_agents_basic)
        await self._record("/tasks/ endpoint", self.test_smoke_tasks)
        await self._record("/schedules/ endpoint", self.test_smoke_schedules)
        await self._record("/notifications/count endpoint", self.test_smoke_notifications)
        await self._record("/settings/ endpoint", self.test_smoke_settings)

        print("\n── RLS Regression Tests ─────────────────────────────────────────")
        await self._record(
            "agents visible after forced last_active_at refresh",
            self.test_rls_regression_after_forced_activity_update,
        )
        await self._record(
            "3 consecutive forced-activity cycles stay stable",
            self.test_repeated_activity_update_stable,
        )

        print("\n── Concurrency Test (asyncio.gather fix) ────────────────────────")
        await self._record(
            "10 concurrent /agents/ requests all succeed",
            self.test_concurrent_agent_list,
        )

        # Summary
        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        total = len(self.results)

        print("\n" + "=" * 70)
        if failed == 0:
            print(f"  ✅ ALL TESTS PASSED  ({passed}/{total})")
        else:
            print(f"  ❌ {failed} TEST(S) FAILED  ({passed}/{total} passed)")
            print()
            for r in self.results:
                if not r.passed:
                    print(f"     ❌ {r.name}")
                    print(f"        → {r.detail}")
        print("=" * 70 + "\n")

        sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(TestSuite().run_all())
