"""Team model — a persistent, named group of agents with a designated lead."""

from sqlalchemy import String, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # List of agent IDs that belong to the team
    member_agent_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # Designated lead agent (one of the members) — None until assigned
    lead_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    # Creator
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
