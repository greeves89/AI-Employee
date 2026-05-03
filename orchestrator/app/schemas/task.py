from datetime import datetime

from pydantic import BaseModel

from app.models.task import TaskPriority, TaskStatus


class TaskCreate(BaseModel):
    title: str
    prompt: str
    priority: int = TaskPriority.NORMAL
    agent_id: str | None = None  # None = auto-assign
    model: str | None = None
    parent_task_id: str | None = None  # For subtask hierarchies
    created_by_agent: str | None = None  # Agent that delegated this task


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
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None
    num_turns: int | None
    parent_task_id: str | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TaskBatchCreate(BaseModel):
    """Create multiple tasks in a single call (parallel sub-agent spawning)."""
    tasks: list[TaskCreate]
    parent_task_id: str | None = None  # All tasks become subtasks of this parent
    created_by_agent: str | None = None  # Agent that spawned this batch


class TaskBatchResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int
    parent_task_id: str | None = None


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int
