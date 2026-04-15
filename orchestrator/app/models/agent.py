import enum

from sqlalchemy import JSON, Enum, Float, ForeignKey, Integer, String
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
    # SHA-256 hex of the plaintext webhook token. Set by
    # /agents/{id}/webhook/rotate; plaintext is shown once and never stored.
    webhook_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    tasks: Mapped[list["Task"]] = relationship(back_populates="agent")  # noqa: F821
    owner: Mapped["User | None"] = relationship("User")  # noqa: F821
