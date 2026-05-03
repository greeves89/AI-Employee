"""URL Allowlist — restricts which URLs agents may access.

Templates provide predefined URL pattern sets (Developer, Research, Marketing, Minimal).
Applying a template to an agent copies its entries into the per-agent allowlist.
Non-matching URLs trigger an approval request to the user.
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class UrlAllowlistTemplate(Base, TimestampMixin):
    __tablename__ = "url_allowlist_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    entries: Mapped[list["UrlAllowlistTemplateEntry"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", lazy="selectin",
    )


class UrlAllowlistTemplateEntry(Base, TimestampMixin):
    __tablename__ = "url_allowlist_template_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("url_allowlist_templates.id", ondelete="CASCADE"))
    url_pattern: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(200), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped["UrlAllowlistTemplate"] = relationship(back_populates="entries")


class AgentUrlAllowlist(Base, TimestampMixin):
    __tablename__ = "agent_url_allowlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    url_pattern: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
