"""Second Brain — admin-managed, department-shared knowledge vaults.

A SecondBrain is a DB-managed mount-catalog entry: a shared folder of Markdown
files under ``/srv/secondbrain/<slug>/`` that gets bind-mounted into assigned
agents as ``/mnt/brains/<slug>``. All read/write access control reuses the
existing mount-label machinery (``user_mount_access`` +
``custom_roles.permissions.mount_labels``), keyed on the brain's ``label`` —
so creating a brain makes it appear in the mount-permissions UI and the agent
mount selector immediately, without a .env edit or orchestrator restart.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SecondBrain(Base):
    __tablename__ = "second_brains"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Mount label that every access grant hangs on, e.g. "brain-it_operations".
    label: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # display / department name
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    host_path: Mapped[str] = mapped_column(String, nullable=False)        # /srv/secondbrain/<slug>  (never exposed to UI)
    container_path: Mapped[str] = mapped_column(String, nullable=False)   # /mnt/brains/<slug>
    default_mode: Mapped[str] = mapped_column(String, nullable=False, default="rw")  # "ro" | "rw" — upper bound; per-user/role narrows it
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
