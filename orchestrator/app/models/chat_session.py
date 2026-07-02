"""Per-chat-session metadata (title override + pin).

A "chat" in the UI is a group of ChatMessages sharing a session_id. The title
shown in the tabs is normally derived from the first user message. This table
adds the OPTIONAL user overrides that can't be derived: a custom title (rename)
and a pinned flag. Rows are created lazily the first time a session is renamed
or pinned — a session without a row simply uses its derived preview and is
unpinned, so nothing breaks for existing chats.
"""

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("agent_id", "session_id", name="uq_chat_sessions_agent_session"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    # Custom title set via rename. None → the UI uses the derived first-message preview.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
