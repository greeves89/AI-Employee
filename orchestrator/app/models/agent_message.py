"""Inter-agent message model — stores all messages between agents.

Supports structured messaging with message types and reply threading:
- message_type: question, response, handoff, notification, status_update
- reply_to: links a response to the original message for conversation threading
- message_id: stable UUID for external references (separate from auto-increment PK)
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    from_agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    from_agent_name: Mapped[str] = mapped_column(String, nullable=False)
    to_agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str | None] = mapped_column(String, nullable=True, default="message")  # message, question, response, handoff, notification, status_update
    reply_to: Mapped[str | None] = mapped_column(String, nullable=True)  # message_id of the message being replied to
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
