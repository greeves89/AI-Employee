"""Reusable AI model accounts — admin-managed, org-wide.

An AIAccount bundles an LLM provider credential + model config so it can be
created once by an admin and then referenced by many agents via
``agents.ai_account_id`` — instead of typing an inline ``llm_config`` per agent.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIAccount(Base):
    __tablename__ = "ai_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    # azure-openai | openai | anthropic | google | bedrock | foundry | vertex | ollama | lm-studio
    provider_type: Mapped[str] = mapped_column(String, nullable=False)
    api_endpoint: Mapped[str | None] = mapped_column(String, nullable=True)
    # Fernet-encrypted; nullable for keyless providers (ollama, lm-studio)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    # Provider-specific extras: azure api_version/deployment, aws_region,
    # vertex_project, max_tokens, temperature, ...
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
