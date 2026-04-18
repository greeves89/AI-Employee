from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator


_CRON_PRESETS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}


def _validate_cron(expr: str) -> str:
    """Validate a cron expression using croniter if available."""
    expr = _CRON_PRESETS.get(expr, expr)
    try:
        from croniter import croniter
        if not croniter.is_valid(expr):
            raise ValueError(f"Invalid cron expression: {expr!r}")
    except ImportError:
        pass  # croniter not installed yet; validate at runtime
    return expr


class ScheduleCreate(BaseModel):
    name: str
    prompt: str
    interval_seconds: int = 0
    cron_expression: str | None = None
    priority: int = 1
    agent_id: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> "ScheduleCreate":
        if not self.cron_expression and self.interval_seconds < 60:
            raise ValueError("Provide either a valid cron_expression or interval_seconds >= 60")
        if self.cron_expression:
            self.cron_expression = _validate_cron(self.cron_expression)
        return self


class ScheduleUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    priority: int | None = None
    agent_id: str | None = None
    model: str | None = None

    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, v: int | None) -> int | None:
        if v is not None and v < 60:
            raise ValueError("Interval must be at least 60 seconds")
        return v

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_cron(v)
        return v


class ScheduleResponse(BaseModel):
    id: str
    name: str
    prompt: str
    interval_seconds: int
    cron_expression: str | None
    priority: int
    agent_id: str | None
    model: str | None
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    total_runs: int
    success_count: int
    fail_count: int
    success_rate: float = 0.0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_schedule(cls, schedule) -> "ScheduleResponse":
        rate = 0.0
        if schedule.total_runs > 0:
            rate = round(schedule.success_count / schedule.total_runs, 2)
        return cls(
            id=schedule.id,
            name=schedule.name,
            prompt=schedule.prompt,
            interval_seconds=schedule.interval_seconds,
            cron_expression=getattr(schedule, "cron_expression", None),
            priority=schedule.priority,
            agent_id=schedule.agent_id,
            model=schedule.model,
            enabled=schedule.enabled,
            next_run_at=schedule.next_run_at,
            last_run_at=schedule.last_run_at,
            total_runs=schedule.total_runs,
            success_count=schedule.success_count,
            fail_count=schedule.fail_count,
            success_rate=rate,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
        )


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]
    total: int
