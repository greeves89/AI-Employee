"""Pydantic schemas for agent TODOs."""

from datetime import datetime

from pydantic import BaseModel, Field


class TodoCreate(BaseModel):
    title: str
    description: str | None = None
    task_id: str | None = None
    project: str | None = None
    project_path: str | None = None
    priority: int = Field(default=3, ge=1, le=5)
    sort_order: int = 0


class TodoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None  # "pending", "in_progress", "completed"
    project: str | None = None
    project_path: str | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    sort_order: int | None = None


class TodoBulkItem(BaseModel):
    title: str
    description: str | None = None
    status: str = "pending"  # "pending", "in_progress", "completed"
    priority: int = Field(default=3, ge=1, le=5)
    project: str | None = None
    project_path: str | None = None


class TodoBulkUpdate(BaseModel):
    """Replace all TODOs for a given task_id (or general if task_id is null)."""
    task_id: str | None = None
    project: str | None = None
    project_path: str | None = None
    todos: list[TodoBulkItem]


class TodoResponse(BaseModel):
    id: int
    agent_id: str
    task_id: str | None
    project: str | None = None
    project_path: str | None = None
    title: str
    description: str | None
    status: str
    priority: int
    sort_order: int
    created_at: datetime
    updated_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TodoListResponse(BaseModel):
    todos: list[TodoResponse]
    total: int
    pending: int
    in_progress: int
    completed: int
    projects: list[str] = []
