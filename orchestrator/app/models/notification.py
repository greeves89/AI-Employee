"""Notification system - agents can notify users about events."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String, default="info")  # info, warning, error, success, approval
    title: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String, default="normal")  # low, normal, high, urgent
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    action_url: Mapped[str | None] = mapped_column(String, nullable=True)  # optional link to navigate to
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # extra data (approval options, etc.)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
