"""Approval rules — user-defined triggers that require explicit agent-to-user approval.

Rules tell agents WHEN to call the request_approval MCP tool before acting.
Examples: "Ask before spending more than 50 EUR", "Ask before sending emails to external recipients".
"""

from sqlalchemy import String, Text, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ApprovalRule(Base, TimestampMixin):
    __tablename__ = "approval_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Short label (shown in UI, injected into agent prompt)
    name: Mapped[str] = mapped_column(String(200))
    # The actual rule text that the agent will read (e.g. "Ask before spending > 50 EUR")
    description: Mapped[str] = mapped_column(Text)
    # Category: money, email, file_delete, external_api, purchase, custom
    category: Mapped[str] = mapped_column(String(50), default="custom")
    # Optional threshold value (e.g. 50 for "> 50 EUR")
    threshold: Mapped[float | None] = mapped_column(nullable=True)
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Applies to all agents or specific agent (null = all)
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Creator
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
