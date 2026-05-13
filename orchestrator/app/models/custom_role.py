"""Custom roles with admin-configurable permissions.

Permissions JSON shape:
{
  "max_agents": int | null,            # null = unlimited
  "template_ids": int[] | null,        # null = all templates allowed
  "llm_providers": string[] | null,    # null = all providers allowed
  "mount_labels": string[] | null,     # null = inherits user_mount_access only
  "url_host_patterns": string[] | null,# null = no extra restrictions
  "menu_paths": string[] | null        # null = all menus visible
}

null means "no restriction". Empty list [] means "deny all".
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CustomRole(Base):
    __tablename__ = "custom_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
