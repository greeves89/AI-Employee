"""Autonomy preset rules — template rule sets applied when an agent is assigned a level (L1-L4).

Stored in DB so admins can add/edit/remove rules per level via the UI.
On startup, defaults are seeded from AUTONOMY_DEFAULTS if a level has no entries yet.
"""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AutonomyPresetRule(Base, TimestampMixin):
    __tablename__ = "autonomy_preset_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # l1 / l2 / l3 / l4
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="custom")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
