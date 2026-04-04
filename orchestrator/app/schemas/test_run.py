"""Test run schemas for API responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TestResultItem(BaseModel):
    name: str
    category: str
    status: str
    duration_ms: int
    error: str | None = None
    details: dict[str, Any] = {}
    github_issue_url: str | None = None


class TestRunResponse(BaseModel):
    id: int
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    total: int
    passed: int
    failed: int
    skipped: int
    status: str
    summary: str | None
    github_issues_created: int
    results: list[TestResultItem] | None = None
    performance: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class TestRunListResponse(BaseModel):
    test_runs: list[TestRunResponse]
    total: int


class HealthDashboardResponse(BaseModel):
    """Aggregated health data for the admin dashboard."""
    # Current status
    overall_status: str  # healthy, degraded, critical
    uptime_pct: float | None  # % of passed runs in last 30 days

    # Latest run
    latest_run: TestRunResponse | None

    # Trends (last 14 runs)
    pass_rate_trend: list[float]  # % passed per run
    response_time_trend: list[dict[str, Any]]  # avg response times
    failure_categories: dict[str, int]  # category -> failure count

    # Agent health from improvement engine
    agent_ratings: list[dict[str, Any]]  # [{agent_id, name, avg_rating, status}]

    # Open issues
    open_auto_issues: int

    # Cost summary
    total_cost_7d: float | None
    total_tasks_7d: int
