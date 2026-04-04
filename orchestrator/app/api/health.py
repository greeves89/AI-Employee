"""Health & Self-Test API — test runs, dashboard, on-demand testing."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.agent import Agent
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.test_run import TestRun
from app.schemas.test_run import (
    HealthDashboardResponse,
    TestRunListResponse,
    TestRunResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


def _run_to_response(run: TestRun) -> dict:
    return {
        "id": run.id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "duration_ms": run.duration_ms,
        "total": run.total,
        "passed": run.passed,
        "failed": run.failed,
        "skipped": run.skipped,
        "status": run.status,
        "summary": run.summary,
        "github_issues_created": run.github_issues_created,
        "results": run.results,
        "performance": run.performance,
    }


@router.get("/test-runs", response_model=TestRunListResponse)
async def list_test_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List test runs, newest first."""
    count_result = await db.execute(select(func.count()).select_from(TestRun))
    total = count_result.scalar() or 0

    query = (
        select(TestRun)
        .order_by(TestRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    runs = list(result.scalars().all())

    return {
        "test_runs": [_run_to_response(r) for r in runs],
        "total": total,
    }


@router.get("/test-runs/latest", response_model=TestRunResponse)
async def get_latest_test_run(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent test run."""
    result = await db.execute(
        select(TestRun).order_by(TestRun.started_at.desc()).limit(1)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="No test runs yet")
    return _run_to_response(run)


@router.get("/test-runs/{run_id}", response_model=TestRunResponse)
async def get_test_run(
    run_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific test run with full details."""
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")
    return _run_to_response(run)


@router.post("/test-runs/trigger", response_model=TestRunResponse)
async def trigger_test_run(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a test run (async — returns immediately with running status)."""
    import asyncio
    from app.services.self_test_service import SelfTestService

    service = SelfTestService()
    # Run in background
    asyncio.create_task(service.execute_test_run())

    # Return a placeholder
    return {
        "id": 0,
        "started_at": datetime.now(timezone.utc),
        "completed_at": None,
        "duration_ms": None,
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "status": "running",
        "summary": "Test run triggered — refresh to see results.",
        "github_issues_created": 0,
        "results": None,
        "performance": None,
    }


@router.get("/dashboard", response_model=HealthDashboardResponse)
async def get_health_dashboard(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated health dashboard data for the admin UI."""

    # --- Latest test run ---
    latest_result = await db.execute(
        select(TestRun).order_by(TestRun.started_at.desc()).limit(1)
    )
    latest_run = latest_result.scalar_one_or_none()

    # --- Pass rate trend (last 14 runs) ---
    trend_result = await db.execute(
        select(TestRun)
        .where(TestRun.status.in_(["passed", "failed"]))
        .order_by(TestRun.started_at.desc())
        .limit(14)
    )
    trend_runs = list(reversed(list(trend_result.scalars().all())))

    pass_rate_trend = []
    response_time_trend = []
    for run in trend_runs:
        if run.total > 0:
            pass_rate_trend.append(round(run.passed / run.total * 100, 1))
        else:
            pass_rate_trend.append(0)
        # Extract avg response time from performance snapshot
        perf = run.performance or {}
        response_time_trend.append({
            "date": run.started_at.isoformat() if run.started_at else None,
            "avg_ms": perf.get("avg_test_duration_ms", 0),
            "endpoint_times": perf.get("endpoint_response_times", {}),
        })

    # --- Failure categories (from last 14 runs) ---
    failure_categories: dict[str, int] = {}
    for run in trend_runs:
        for test in (run.results or []):
            if test.get("status") == "failed":
                cat = test.get("category", "unknown")
                failure_categories[cat] = failure_categories.get(cat, 0) + 1

    # --- Uptime % (last 30 days) ---
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    uptime_result = await db.execute(
        select(TestRun)
        .where(TestRun.started_at >= thirty_days_ago)
        .where(TestRun.status.in_(["passed", "failed"]))
    )
    all_runs_30d = list(uptime_result.scalars().all())
    if all_runs_30d:
        passed_runs = sum(1 for r in all_runs_30d if r.status == "passed")
        uptime_pct = round(passed_runs / len(all_runs_30d) * 100, 1)
    else:
        uptime_pct = None

    # --- Overall status ---
    if not latest_run:
        overall_status = "unknown"
    elif latest_run.status == "passed":
        overall_status = "healthy"
    elif latest_run.failed <= 2:
        overall_status = "degraded"
    else:
        overall_status = "critical"

    # --- Agent ratings ---
    agent_ratings_list = []
    agents_result = await db.execute(select(Agent))
    agents = list(agents_result.scalars().all())
    for agent in agents:
        config = agent.config or {}
        improvement = config.get("improvement", {})
        avg_result = await db.execute(
            select(func.avg(TaskRating.rating)).where(TaskRating.agent_id == agent.id)
        )
        avg = avg_result.scalar()
        agent_ratings_list.append({
            "agent_id": agent.id,
            "name": agent.name,
            "avg_rating": round(float(avg), 2) if avg else None,
            "status": improvement.get("status", "no_data"),
            "total_ratings": improvement.get("total_ratings", 0),
        })

    # --- Cost & task summary (7 days) ---
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    cost_result = await db.execute(
        select(
            func.sum(Task.cost_usd),
            func.count(Task.id),
        ).where(Task.completed_at >= seven_days_ago)
    )
    cost_row = cost_result.one()
    total_cost_7d = round(float(cost_row[0]), 4) if cost_row[0] else None
    total_tasks_7d = cost_row[1] or 0

    # --- Open auto-test issues (count from latest run) ---
    open_issues = 0
    if latest_run and latest_run.results:
        open_issues = sum(
            1 for t in latest_run.results
            if t.get("github_issue_url") and t.get("status") == "failed"
        )

    return {
        "overall_status": overall_status,
        "uptime_pct": uptime_pct,
        "latest_run": _run_to_response(latest_run) if latest_run else None,
        "pass_rate_trend": pass_rate_trend,
        "response_time_trend": response_time_trend,
        "failure_categories": failure_categories,
        "agent_ratings": agent_ratings_list,
        "open_auto_issues": open_issues,
        "total_cost_7d": total_cost_7d,
        "total_tasks_7d": total_tasks_7d,
    }
