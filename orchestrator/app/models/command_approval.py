"""Command approval system - agents request user approval before executing commands."""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class CommandApproval(Base):
    __tablename__ = "command_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    command: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String, default="medium")  # low, medium, high, critical
    status: Mapped[str] = mapped_column(String, default=ApprovalStatus.PENDING, index=True)
    user_response: Mapped[str | None] = mapped_column(String, nullable=True)  # user feedback on deny
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # extra context
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
