"""Command Policy model — DB-backed command filtering rules."""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CommandPolicy(Base, TimestampMixin):
    __tablename__ = "command_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    pattern: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(String(20), default="blocked")
    scope: Mapped[str] = mapped_column(String(20), default="global")
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
