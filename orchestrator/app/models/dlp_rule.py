"""DLP egress rule (#388): per-class (optionally per-agent) action for outbound text.

Resolution precedence when a class is detected in an outgoing message:
agent-specific rule > global rule (agent_id NULL) > built-in default. ``action`` is
one of ``allow`` | ``log`` | ``mask`` | ``block``. Seeded with global defaults on
startup; admins manage overrides via the /dlp API.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DlpRule(Base):
    __tablename__ = "dlp_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One of app.core.dlp.CLASSES: secret | iban | credit_card | email | de_tax_id
    pii_class: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # NULL = global rule; otherwise scoped to one agent.
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # allow | log | mask | block
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
