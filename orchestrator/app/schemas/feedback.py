"""Feedback schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    category: str = "general"


class FeedbackUpdate(BaseModel):
    status: str | None = None
    admin_notes: str | None = None


class FeedbackResponse(BaseModel):
    id: int
    user_id: str
    user_name: str | None
    title: str
    description: str | None
    category: str
    status: str
    admin_notes: str | None
    github_issue_url: str | None
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class FeedbackListResponse(BaseModel):
    feedback: list[FeedbackResponse]
    total: int
    pending: int
    reviewed: int
    in_progress: int
    closed: int
