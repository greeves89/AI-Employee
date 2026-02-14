from datetime import datetime

from pydantic import BaseModel, field_validator


class ScheduleCreate(BaseModel):
    name: str
    prompt: str
    interval_seconds: int
    priority: int = 1
    agent_id: str | None = None
    model: str | None = None

    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v < 60:
            raise ValueError("Interval must be at least 60 seconds")
        return v


class ScheduleUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    interval_seconds: int | None = None
    priority: int | None = None
    agent_id: str | None = None
    model: str | None = None

    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, v: int | None) -> int | None:
        if v is not None and v < 60:
            raise ValueError("Interval must be at least 60 seconds")
        return v


class ScheduleResponse(BaseModel):
    id: str
    name: str
    prompt: str
    interval_seconds: int
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
