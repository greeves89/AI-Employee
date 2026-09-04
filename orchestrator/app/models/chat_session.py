"""Per-chat-session metadata (title override + pin + reasoning level).

A "chat" in the UI is a group of ChatMessages sharing a session_id. The title
shown in the tabs is normally derived from the first user message. This table
adds the OPTIONAL user overrides that can't be derived: a custom title (rename),
a pinned flag and the chosen reasoning level. Rows are created lazily the first
time a session is renamed, pinned or gets a level — a session without a row
simply uses its derived preview, is unpinned and thinks at the harness default,
so nothing breaks for existing chats.
"""

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# The only reasoning levels that may ever be stored or forwarded to an agent.
# Single source of truth — ws.py (per-message whitelist) and agents.py (PATCH
# validation) import this so the two gates can't drift apart.
# "xhigh" und "max" sind ZWEI Stufen, nicht eine: Die GPT-5.6-Familie kennt
# oberhalb von xhigh noch max (am Endpunkt geprueft, alle drei Modelle nehmen es
# an). Vor 1.313.0 hiess die oberste Stufe "max" und MEINTE xhigh — bestehende
# Einstellungen werden deshalb einmalig auf "xhigh" umgeschrieben, sonst waeren
# alle Nutzer ungefragt auf der neuen, teureren Stufe gelandet.
REASONING_LEVELS = ("off", "low", "medium", "high", "xhigh", "max")


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
    # One of REASONING_LEVELS. None → "Auto", the harness default decides.
    reasoning_level: Mapped[str | None] = mapped_column(String, nullable=True)
