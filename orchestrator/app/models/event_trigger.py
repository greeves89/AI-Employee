"""Event Triggers - conditional rules that fire agent tasks from webhook events.

An EventTrigger defines: "When a webhook arrives matching these conditions,
create a task for this agent using this prompt template."

Conditions are evaluated against the webhook payload using JSONPath-like
field matching. Prompt templates support {{payload.field}} interpolation.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EventTrigger(Base, TimestampMixin):
    __tablename__ = "event_triggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    # Match criteria (all non-null fields must match for trigger to fire)
    source_filter: Mapped[str | None] = mapped_column(String, nullable=True)      # e.g. "github", "stripe"
    event_type_filter: Mapped[str | None] = mapped_column(String, nullable=True)   # e.g. "pull_request", "payment"
    payload_conditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {"action": "opened", "pull_request.draft": false}

    # Action
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)  # supports {{payload.field}} interpolation
    priority: Mapped[int] = mapped_column(Integer, default=5)
    model: Mapped[str | None] = mapped_column(String, nullable=True)

    # State
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
