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


class FeedbackWidgetMessage(BaseModel):
    role: str = Field(..., pattern="^(user|bot)$")
    text: str = ""


class FeedbackWidgetContext(BaseModel):
    """Kontext des gepinnten Elements. Bewusst OHNE user-Feld: die Attribution
    kommt ausschliesslich aus der validierten Session (require_auth) — ein
    mitgeschickter Username wuerde ignoriert."""

    model_config = {"extra": "ignore"}

    page: str | None = Field(None, max_length=500)
    element_label: str | None = Field(None, max_length=200)
    selector: str | None = Field(None, max_length=500)
    sentiment: str | None = Field(None, max_length=20)
    kategorie: str | None = Field(None, max_length=20)


class FeedbackWidgetIn(BaseModel):
    messages: list[FeedbackWidgetMessage] = Field(default_factory=list)
    context: FeedbackWidgetContext = Field(default_factory=FeedbackWidgetContext)
    # base64-dataURL (PNG) des sichtbaren Viewports, optional
    screenshot: str | None = None


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
    page: str | None = None
    element_label: str | None = None
    selector: str | None = None
    sentiment: str | None = None
    md_file: str | None = None
    screenshot_file: str | None = None
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
