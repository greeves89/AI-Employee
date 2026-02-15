"""Webhook events - external services can trigger agent tasks."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)  # github, stripe, custom, etc.
    event_type: Mapped[str] = mapped_column(String)  # push, payment, etc.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="received")  # received, processing, completed, failed
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)  # linked task if one was created
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
