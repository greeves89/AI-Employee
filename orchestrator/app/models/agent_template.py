"""Agent template model for pre-configured agent blueprints."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
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
    skill_ids: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_template: Mapped[str] = mapped_column(Text, default="")
    # Daueraufgaben, die diese Vorlage mitbringt — damit ein neuer Agent nicht bei
    # null anfaengt und einzeln gebrieft werden muss. Format wie in
    # core/responsibilities: [{title, rhythm, priority, notes}].
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    claude_md: Mapped[str] = mapped_column(Text, default="")
    # Agenten dieser Vorlage bauen Windows-Programme (.exe): eigenes Volume fuer
    # die Werkzeugketten + Anleitung. Siehe agent/scripts/build-tools.
    # Nur ueber die mitgelieferten Vorlagen gesetzt (Start-Abgleich); die
    # Vorlagen-API nimmt das Feld bewusst nicht an. Wer die Vorlage nutzen darf,
    # regelt can_use_template je Rolle.
    build_tools: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Meta
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )

    # Publication — only published templates are visible to non-admin users
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
