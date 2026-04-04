"""Task rating schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class TaskRatingCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Rating 1-5")
    comment: str | None = None


class TaskRatingResponse(BaseModel):
    id: int
    task_id: str
    agent_id: str
    user_id: str | None
    rating: int
    comment: str | None
    task_cost_usd: float | None
    task_duration_ms: int | None
    task_num_turns: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentRatingsResponse(BaseModel):
    ratings: list[TaskRatingResponse]
    total: int
    average_rating: float | None


class ImprovementReport(BaseModel):
    agent_id: str
    agent_name: str
    total_ratings: int
    average_rating: float | None
    rating_trend: list[float]  # rolling avg over time windows
    cost_trend: list[float | None]
    duration_trend: list[int | None]
    top_issues: list[str]  # most common negative comments
    summary: str
