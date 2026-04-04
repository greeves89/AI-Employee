"""Self-Test Service — automated daily health checks, integration tests, and self-improvement.

Runs as a scheduled background job. Executes tests, records results,
creates GitHub issues for failures, and sends a Telegram digest.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.models.agent import Agent
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.test_run import TestRun

logger = logging.getLogger(__name__)

# Internal API base for self-testing
API_BASE = "http://127.0.0.1:8000/api/v1"

# Run daily at 06:00 UTC
RUN_INTERVAL_SECONDS = 86400


class TestResult:
    """Result of a single test."""

    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.status = "pending"  # passed, failed, skipped, error
        self.duration_ms = 0
        self.error: str | None = None
        self.details: dict[str, Any] = {}
        self.github_issue_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "details": self.details,
            "github_issue_url": self.github_issue_url,
        }


class SelfTestService:
    """Automated self-testing and monitoring service."""

    def __init__(self):
        self._running = False

    async def run(self) -> None:
        """Main loop — runs tests on schedule."""
        self._running = True
        logger.info("[SelfTest] Service started — running daily health checks")

        # Wait 2 minutes before first run
        await asyncio.sleep(120)

        while self._running:
            try:
                await self.execute_test_run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SelfTest] Unhandled error: {e}")

            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def execute_test_run(self) -> dict:
        """Execute a full test run and return results. Can be called on-demand via API."""
        start = time.monotonic()
        results: list[TestResult] = []

        async with async_session_factory() as db:
            # Create test run record
            test_run = TestRun(
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            db.add(test_run)
            await db.commit()
            await db.refresh(test_run)

            try:
                # Run all test categories
                results.extend(await self._run_health_checks(db))
                results.extend(await self._run_api_endpoint_tests())
                results.extend(await self._run_integration_tests(db))
                results.extend(await self._run_performance_tests())
                results.extend(await self._run_data_integrity_checks(db))

                # Calculate totals
                elapsed_ms = int((time.monotonic() - start) * 1000)
                passed = sum(1 for r in results if r.status == "passed")
                failed = sum(1 for r in results if r.status == "failed")
                skipped = sum(1 for r in results if r.status == "skipped")

                # Create GitHub issues for failures
                issues_created = 0
                if failed > 0:
                    issues_created = await self._create_github_issues(db, results, test_run.id)

                # Build performance snapshot
                perf_snapshot = self._build_performance_snapshot(results)

                # Build summary
                summary = self._build_summary(results, elapsed_ms, issues_created)

                # Update test run
                test_run.completed_at = datetime.now(timezone.utc)
                test_run.duration_ms = elapsed_ms
                test_run.total = len(results)
                test_run.passed = passed
                test_run.failed = failed
                test_run.skipped = skipped
                test_run.results = [r.to_dict() for r in results]
                test_run.performance = perf_snapshot
                test_run.github_issues_created = issues_created
                test_run.status = "passed" if failed == 0 else "failed"
                test_run.summary = summary

                await db.commit()

                # Send Telegram digest
                await self._send_telegram_digest(summary)

                # Create notification
                notif = Notification(
                    agent_id="system",
                    type="success" if failed == 0 else "warning",
                    title=f"Self-Test: {passed}/{len(results)} passed",
                    message=summary,
                    priority="normal" if failed == 0 else "high",
                    action_url="/health",
                    meta={"type": "self_test", "test_run_id": test_run.id},
                )
                db.add(notif)
                await db.commit()

                logger.info(
                    f"[SelfTest] Run #{test_run.id}: {passed}/{len(results)} passed, "
                    f"{failed} failed, {elapsed_ms}ms"
                )

                return test_run.results

            except Exception as e:
                test_run.status = "error"
                test_run.summary = f"Test run crashed: {e}"
                test_run.completed_at = datetime.now(timezone.utc)
                await db.commit()
                raise

    # ─── HEALTH CHECKS ────────────────────────────────────────

    async def _run_health_checks(self, db: AsyncSession) -> list[TestResult]:
        """Core infrastructure health checks."""
        results = []

        # 1. Database connection
        r = TestResult("db_connection", "health")
        start = time.monotonic()
        try:
            await db.execute(text("SELECT 1"))
            r.status = "passed"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 2. Alembic migration sync
        r = TestResult("alembic_schema_sync", "health")
        start = time.monotonic()
        try:
            result = await db.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar()
            # Just verify alembic_version exists and has a value
            if current:
                r.status = "passed"
                r.details = {"current_revision": current}
            else:
                r.status = "failed"
                r.error = "No alembic version found"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 3. Redis connection
        r = TestResult("redis_connection", "health")
        start = time.monotonic()
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.redis_url)
            pong = await client.ping()
            r.status = "passed" if pong else "failed"
            await client.aclose()
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 4. Docker socket
        r = TestResult("docker_socket", "health")
        start = time.monotonic()
        try:
            import docker
            client = docker.from_env()
            client.ping()
            r.status = "passed"
            r.details = {"server_version": client.version().get("Version", "unknown")}
            client.close()
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 5. Agent count and state check
        r = TestResult("agents_healthy", "health")
        start = time.monotonic()
        try:
            result = await db.execute(select(Agent))
            agents = list(result.scalars().all())
            r.details = {
                "total": len(agents),
                "states": {}
            }
            for a in agents:
                state = str(a.state)
                r.details["states"][state] = r.details["states"].get(state, 0) + 1
            r.status = "passed"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 6. Anthropic API key validation
        r = TestResult("anthropic_api_key", "health")
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Simple auth check — count tokens on a minimal prompt
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key or "",
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                if resp.status_code in (200, 529):
                    # 200 = works, 529 = overloaded but auth OK
                    r.status = "passed"
                    r.details = {"status_code": resp.status_code}
                elif resp.status_code == 401:
                    r.status = "failed"
                    r.error = "Anthropic API key invalid or expired (401)"
                elif resp.status_code == 403:
                    r.status = "failed"
                    r.error = "Anthropic API key forbidden (403) — check permissions"
                else:
                    r.status = "passed"  # Other codes (rate limit etc) mean auth works
                    r.details = {"status_code": resp.status_code}
        except Exception as e:
            r.status = "failed"
            r.error = f"Could not reach Anthropic API: {e}"
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        return results

    # ─── API ENDPOINT TESTS ───────────────────────────────────

    async def _run_api_endpoint_tests(self) -> list[TestResult]:
        """Test that all API endpoints are reachable."""
        results = []

        # Endpoints that should return 200 without auth (or 401 which means they're alive)
        endpoints = [
            ("GET", "/version", [200]),
            ("GET", "/agents", [200, 401]),
            ("GET", "/tasks", [200, 401]),
            ("GET", "/notifications", [200, 401]),
            ("GET", "/feedback", [200, 401, 403]),
            ("GET", "/schedules", [200, 401]),
            ("GET", "/ratings/agents/test/ratings", [200, 401, 404]),
            ("GET", "/todos", [200, 401]),
            ("GET", "/settings", [200, 401]),
            ("GET", "/knowledge", [200, 401]),
            ("GET", "/memory", [200, 401]),
        ]

        async with httpx.AsyncClient(timeout=10.0) as client:
            for method, path, valid_codes in endpoints:
                r = TestResult(f"api_{path.strip('/').replace('/', '_')}", "api")
                start = time.monotonic()
                try:
                    resp = await client.request(method, f"{API_BASE}{path}")
                    if resp.status_code in valid_codes:
                        r.status = "passed"
                    else:
                        r.status = "failed"
                        r.error = f"Expected {valid_codes}, got {resp.status_code}"
                    r.details = {
                        "status_code": resp.status_code,
                        "response_ms": int((time.monotonic() - start) * 1000),
                    }
                except Exception as e:
                    r.status = "failed"
                    r.error = str(e)
                r.duration_ms = int((time.monotonic() - start) * 1000)
                results.append(r)

        return results

    # ─── INTEGRATION TESTS ────────────────────────────────────

    async def _run_integration_tests(self, db: AsyncSession) -> list[TestResult]:
        """Test integrated workflows."""
        results = []

        # 1. Task lifecycle check: verify recent tasks completed properly
        r = TestResult("task_lifecycle", "integration")
        start = time.monotonic()
        try:
            since = datetime.now(timezone.utc) - timedelta(days=1)
            result = await db.execute(
                select(Task).where(Task.completed_at >= since)
            )
            recent_tasks = list(result.scalars().all())
            completed = [t for t in recent_tasks if t.status == TaskStatus.COMPLETED]
            failed = [t for t in recent_tasks if t.status == TaskStatus.FAILED]

            r.details = {
                "tasks_24h": len(recent_tasks),
                "completed": len(completed),
                "failed": len(failed),
                "success_rate": round(len(completed) / max(len(recent_tasks), 1), 2),
            }
            # Pass if success rate > 50% or no tasks at all
            if not recent_tasks or r.details["success_rate"] > 0.5:
                r.status = "passed"
            else:
                r.status = "failed"
                r.error = f"Task success rate too low: {r.details['success_rate']}"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 2. Rating system health
        r = TestResult("rating_system", "integration")
        start = time.monotonic()
        try:
            result = await db.execute(select(func.count()).select_from(TaskRating))
            total_ratings = result.scalar() or 0
            r.details = {"total_ratings": total_ratings}
            r.status = "passed"  # Just verify table is accessible
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 3. Notification delivery
        r = TestResult("notification_system", "integration")
        start = time.monotonic()
        try:
            result = await db.execute(
                select(func.count()).select_from(Notification)
                .where(Notification.created_at >= datetime.now(timezone.utc) - timedelta(days=1))
            )
            recent_notifs = result.scalar() or 0
            r.details = {"notifications_24h": recent_notifs}
            r.status = "passed"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 4. Agent metrics consistency
        r = TestResult("agent_metrics_consistency", "integration")
        start = time.monotonic()
        try:
            result = await db.execute(select(Agent))
            agents = list(result.scalars().all())
            inconsistencies = []
            for agent in agents:
                config = agent.config or {}
                metrics = config.get("metrics", {})
                total = metrics.get("total", 0)
                success = metrics.get("success", 0)
                fail = metrics.get("fail", 0)
                if total > 0 and (success + fail) != total:
                    inconsistencies.append(f"{agent.name}: total={total} != success({success})+fail({fail})")
            r.details = {"agents_checked": len(agents), "inconsistencies": inconsistencies}
            r.status = "passed" if not inconsistencies else "failed"
            if inconsistencies:
                r.error = f"{len(inconsistencies)} agent(s) have inconsistent metrics"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        return results

    # ─── PERFORMANCE TESTS ────────────────────────────────────

    async def _run_performance_tests(self) -> list[TestResult]:
        """Measure API response times and flag regressions."""
        results = []

        # Baseline response time thresholds (ms)
        thresholds = {
            "/version": 100,
            "/agents": 500,
            "/tasks": 500,
            "/notifications": 500,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            for path, threshold_ms in thresholds.items():
                r = TestResult(f"perf_{path.strip('/').replace('/', '_')}", "performance")
                times = []
                # Run 3 requests, take median
                for _ in range(3):
                    start = time.monotonic()
                    try:
                        await client.get(f"{API_BASE}{path}")
                        times.append(int((time.monotonic() - start) * 1000))
                    except Exception:
                        times.append(threshold_ms * 2)  # Penalty for failed request

                median_ms = sorted(times)[1]  # Median of 3
                r.duration_ms = median_ms
                r.details = {
                    "median_ms": median_ms,
                    "threshold_ms": threshold_ms,
                    "all_times_ms": times,
                }

                if median_ms <= threshold_ms:
                    r.status = "passed"
                elif median_ms <= threshold_ms * 2:
                    r.status = "passed"  # Warn but pass
                    r.details["warning"] = f"Slow: {median_ms}ms > {threshold_ms}ms threshold"
                else:
                    r.status = "failed"
                    r.error = f"Performance regression: {median_ms}ms (threshold: {threshold_ms}ms)"

                results.append(r)

        return results

    # ─── DATA INTEGRITY CHECKS ────────────────────────────────

    async def _run_data_integrity_checks(self, db: AsyncSession) -> list[TestResult]:
        """Check for data anomalies."""
        results = []

        # 1. Orphaned tasks (assigned to non-existent agents)
        r = TestResult("orphaned_tasks", "integrity")
        start = time.monotonic()
        try:
            result = await db.execute(text("""
                SELECT COUNT(*) FROM tasks t
                LEFT JOIN agents a ON t.agent_id = a.id
                WHERE t.agent_id IS NOT NULL AND a.id IS NULL
            """))
            orphaned = result.scalar() or 0
            r.details = {"orphaned_tasks": orphaned}
            r.status = "passed" if orphaned == 0 else "failed"
            if orphaned > 0:
                r.error = f"{orphaned} task(s) assigned to non-existent agents"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 2. Stuck tasks (running for > 1 hour)
        r = TestResult("stuck_tasks", "integrity")
        start = time.monotonic()
        try:
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            result = await db.execute(
                select(func.count()).select_from(Task)
                .where(Task.status == TaskStatus.RUNNING)
                .where(Task.started_at < one_hour_ago)
            )
            stuck = result.scalar() or 0
            r.details = {"stuck_tasks": stuck}
            r.status = "passed" if stuck == 0 else "failed"
            if stuck > 0:
                r.error = f"{stuck} task(s) stuck in RUNNING state for > 1 hour"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        # 3. DB table sizes (for monitoring growth)
        r = TestResult("db_table_sizes", "integrity")
        start = time.monotonic()
        try:
            tables = ["tasks", "agents", "chat_messages", "notifications", "task_ratings", "test_runs"]
            sizes = {}
            for table in tables:
                try:
                    result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    sizes[table] = result.scalar() or 0
                except Exception:
                    sizes[table] = -1  # Table doesn't exist yet
            r.details = {"row_counts": sizes}
            r.status = "passed"
        except Exception as e:
            r.status = "failed"
            r.error = str(e)
        r.duration_ms = int((time.monotonic() - start) * 1000)
        results.append(r)

        return results

    # ─── GITHUB ISSUE CREATION ────────────────────────────────

    async def _create_github_issues(
        self, db: AsyncSession, results: list[TestResult], test_run_id: int
    ) -> int:
        """Create GitHub issues for test failures. Returns count of issues created."""
        failed = [r for r in results if r.status == "failed"]
        if not failed:
            return 0

        # Get GitHub token from OAuth integration
        try:
            from app.models.oauth_integration import OAuthIntegration
            result = await db.execute(
                select(OAuthIntegration).where(OAuthIntegration.provider == "github")
            )
            oauth = result.scalar_one_or_none()
            if not oauth or not oauth.access_token:
                logger.warning("[SelfTest] No GitHub OAuth token — skipping issue creation")
                return 0

            from app.security.encryption import decrypt_value
            token = decrypt_value(oauth.access_token)
        except Exception as e:
            logger.warning(f"[SelfTest] Could not get GitHub token: {e}")
            return 0

        repo = "greeves89/AI-Employee"
        issues_created = 0

        async with httpx.AsyncClient(timeout=15.0) as client:
            for test in failed:
                try:
                    # Check if issue already exists (search by title)
                    search_title = f"[Self-Test] {test.name}"
                    search_resp = await client.get(
                        f"https://api.github.com/search/issues",
                        params={
                            "q": f'repo:{repo} is:issue is:open "{search_title}"',
                        },
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                        },
                    )

                    if search_resp.status_code == 200:
                        existing = search_resp.json().get("total_count", 0)
                        if existing > 0:
                            # Issue already exists — add comment instead
                            issue_num = search_resp.json()["items"][0]["number"]
                            await client.post(
                                f"https://api.github.com/repos/{repo}/issues/{issue_num}/comments",
                                headers={
                                    "Authorization": f"Bearer {token}",
                                    "Accept": "application/vnd.github+json",
                                },
                                json={
                                    "body": (
                                        f"🔄 **Recurring failure** (Test Run #{test_run_id})\n\n"
                                        f"```\n{test.error}\n```\n"
                                        f"Category: `{test.category}`\n"
                                        f"Duration: {test.duration_ms}ms"
                                    ),
                                },
                            )
                            continue

                    # Create new issue
                    body = (
                        f"## Automated Self-Test Failure\n\n"
                        f"**Test:** `{test.name}`\n"
                        f"**Category:** `{test.category}`\n"
                        f"**Test Run:** #{test_run_id}\n"
                        f"**Time:** {datetime.now(timezone.utc).isoformat()}\n\n"
                        f"### Error\n```\n{test.error}\n```\n\n"
                    )
                    if test.details:
                        body += f"### Details\n```json\n{json.dumps(test.details, indent=2)}\n```\n\n"
                    body += "---\n*Created automatically by AI Employee Self-Test Service*"

                    labels = ["auto-test", f"test:{test.category}"]

                    resp = await client.post(
                        f"https://api.github.com/repos/{repo}/issues",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        json={
                            "title": search_title,
                            "body": body,
                            "labels": labels,
                        },
                    )

                    if resp.status_code in (200, 201):
                        test.github_issue_url = resp.json()["html_url"]
                        issues_created += 1
                    else:
                        logger.warning(
                            f"[SelfTest] GitHub issue creation failed: {resp.status_code}"
                        )

                except Exception as e:
                    logger.warning(f"[SelfTest] Failed to create issue for {test.name}: {e}")

        return issues_created

    # ─── AUTO-CLOSE FIXED ISSUES ──────────────────────────────

    async def _auto_close_fixed_issues(
        self, db: AsyncSession, results: list[TestResult]
    ) -> int:
        """Close GitHub issues for tests that now pass (were previously failing)."""
        passed_names = {r.name for r in results if r.status == "passed"}
        if not passed_names:
            return 0

        try:
            from app.models.oauth_integration import OAuthIntegration
            result = await db.execute(
                select(OAuthIntegration).where(OAuthIntegration.provider == "github")
            )
            oauth = result.scalar_one_or_none()
            if not oauth or not oauth.access_token:
                return 0
            from app.security.encryption import decrypt_value
            token = decrypt_value(oauth.access_token)
        except Exception:
            return 0

        repo = "greeves89/AI-Employee"
        closed = 0

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Search for open auto-test issues
            resp = await client.get(
                f"https://api.github.com/search/issues",
                params={"q": f'repo:{repo} is:issue is:open label:auto-test'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if resp.status_code != 200:
                return 0

            for issue in resp.json().get("items", []):
                title = issue.get("title", "")
                # Extract test name from "[Self-Test] test_name"
                if title.startswith("[Self-Test] "):
                    test_name = title[len("[Self-Test] "):]
                    if test_name in passed_names:
                        # Test now passes — close the issue
                        await client.post(
                            f"https://api.github.com/repos/{repo}/issues/{issue['number']}/comments",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json",
                            },
                            json={"body": "✅ This test is now passing. Auto-closing."},
                        )
                        await client.patch(
                            f"https://api.github.com/repos/{repo}/issues/{issue['number']}",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json",
                            },
                            json={"state": "closed"},
                        )
                        closed += 1

        return closed

    # ─── HELPERS ──────────────────────────────────────────────

    def _build_performance_snapshot(self, results: list[TestResult]) -> dict:
        """Build a performance metrics snapshot from test results."""
        perf_tests = [r for r in results if r.category == "performance"]
        endpoint_times = {}
        for r in perf_tests:
            endpoint_times[r.name] = r.details.get("median_ms")

        all_times = [r.duration_ms for r in results if r.duration_ms > 0]
        return {
            "avg_test_duration_ms": round(sum(all_times) / len(all_times)) if all_times else 0,
            "endpoint_response_times": endpoint_times,
            "total_duration_ms": sum(r.duration_ms for r in results),
        }

    def _build_summary(self, results: list[TestResult], elapsed_ms: int, issues_created: int) -> str:
        """Build human-readable summary."""
        passed = sum(1 for r in results if r.status == "passed")
        failed = sum(1 for r in results if r.status == "failed")
        total = len(results)

        # Group failures by category
        failures_by_cat: dict[str, list[str]] = {}
        for r in results:
            if r.status == "failed":
                cat = r.category
                if cat not in failures_by_cat:
                    failures_by_cat[cat] = []
                failures_by_cat[cat].append(f"{r.name}: {r.error}")

        lines = [
            f"🏥 Self-Test Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"",
            f"{'✅' if failed == 0 else '⚠️'} {passed}/{total} Tests bestanden ({elapsed_ms}ms)",
        ]

        if failed > 0:
            lines.append(f"❌ {failed} Fehler:")
            for cat, fails in failures_by_cat.items():
                lines.append(f"  [{cat}]")
                for f in fails:
                    lines.append(f"    • {f}")

        if issues_created > 0:
            lines.append(f"🐛 {issues_created} GitHub Issue(s) erstellt")

        # Performance highlights
        perf_tests = [r for r in results if r.category == "performance"]
        if perf_tests:
            avg_ms = round(sum(r.duration_ms for r in perf_tests) / len(perf_tests))
            lines.append(f"⚡ Avg API Response: {avg_ms}ms")

        return "\n".join(lines)

    async def _send_telegram_digest(self, summary: str) -> None:
        """Send test summary via Telegram."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "http://127.0.0.1:8000/api/v1/notifications/send",
                    json={
                        "type": "info",
                        "title": "Daily Self-Test",
                        "message": summary,
                        "priority": "normal",
                    },
                    headers={"X-Internal": "self-test"},
                )
        except Exception as e:
            logger.warning(f"[SelfTest] Could not send Telegram digest: {e}")

        # Also try direct Redis publish for Telegram bot
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.redis_url)
            await client.publish("telegram:notification", json.dumps({
                "text": summary,
                "chat_id": settings.telegram_chat_id,
            }))
            await client.aclose()
        except Exception:
            pass
