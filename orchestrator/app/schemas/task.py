from datetime import datetime

from pydantic import BaseModel

from app.models.task import TaskPriority, TaskStatus


class TaskCreate(BaseModel):
    title: str
    prompt: str
    priority: int = TaskPriority.NORMAL
    agent_id: str | None = None  # None = auto-assign
    model: str | None = None


class TaskResponse(BaseModel):
    id: str
    title: str
    prompt: str
    status: TaskStatus
    priority: int
    agent_id: str | None
    model: str | None
    result: str | None
    error: str | None
    cost_usd: float | None
    duration_ms: int | None
    num_turns: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int
