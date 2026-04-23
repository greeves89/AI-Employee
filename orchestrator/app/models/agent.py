import enum

from sqlalchemy import JSON, Boolean, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AgentState(str, enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    IDLE = "idle"
    WORKING = "working"
    STOPPED = "stopped"
    ERROR = "error"


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    volume_name: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True, index=True
    )
    state: Mapped[AgentState] = mapped_column(
        Enum(AgentState), default=AgentState.CREATED
    )
    model: Mapped[str] = mapped_column(
        String, default="claude-sonnet-4-6"
    )
    mode: Mapped[str] = mapped_column(
        String, default="claude_code"
    )
    llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    template_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_templates.id"), nullable=True, index=True
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = unlimited
    browser_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    autonomy_level: Mapped[str] = mapped_column(String, default="l3", server_default="l3")
    # SHA-256 hex of the plaintext webhook token. Set by
    # /agents/{id}/webhook/rotate; plaintext is shown once and never stored.
    webhook_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # Per-agent webhook access (enable/disable + bearer token for external tools like n8n/Zapier)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    webhook_token: Mapped[str | None] = mapped_column(String, nullable=True)

    tasks: Mapped[list["Task"]] = relationship(back_populates="agent")  # noqa: F821
    owner: Mapped["User | None"] = relationship("User")  # noqa: F821
