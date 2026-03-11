"""Audit log model - records all privileged/sudo command executions for compliance."""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditEventType(str, Enum):
    COMMAND_EXECUTED = "command_executed"     # sudo/privileged command run
    COMMAND_APPROVED = "command_approved"     # user approved a command
    COMMAND_DENIED = "command_denied"         # user denied a command
    COMMAND_BLOCKED = "command_blocked"       # command blocked by filter
    AGENT_STARTED = "agent_started"           # agent container started
    AGENT_STOPPED = "agent_stopped"           # agent container stopped
    FILE_WRITTEN = "file_written"             # file write operation
    NETWORK_REQUEST = "network_request"       # outbound network call


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)  # AuditEventType value
    command: Mapped[str | None] = mapped_column(Text, nullable=True)   # command or tool name
    outcome: Mapped[str] = mapped_column(String, default="success")    # success, failure, blocked
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # who approved/denied
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # extra context (stdout, stderr, etc.)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
