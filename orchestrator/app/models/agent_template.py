"""Agent template model for pre-configured agent blueprints."""

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentTemplate(Base, TimestampMixin):
    __tablename__ = "agent_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    icon: Mapped[str] = mapped_column(String, default="Bot")
    category: Mapped[str] = mapped_column(String, default="general")

    # Agent configuration
    model: Mapped[str] = mapped_column(String, default="claude-sonnet-4-6")
    role: Mapped[str] = mapped_column(Text, default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    integrations: Mapped[list] = mapped_column(JSON, default=list)
    mcp_server_ids: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_template: Mapped[str] = mapped_column(Text, default="")

    # Meta
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
