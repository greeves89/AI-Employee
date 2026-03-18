"""OAuth integration model - stores encrypted OAuth tokens per provider."""

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OAuthProvider(str, enum.Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    APPLE = "apple"
    GITHUB = "github"
    ANTHROPIC = "anthropic"


class OAuthIntegration(Base, TimestampMixin):
    __tablename__ = "oauth_integrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[OAuthProvider] = mapped_column(
        Enum(OAuthProvider), nullable=False, unique=True, index=True
    )
    # Encrypted with Fernet (ENCRYPTION_KEY)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String, default="Bearer")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str] = mapped_column(String, default="")  # space-separated
    # User-friendly account identifier (e.g., email address)
    account_label: Mapped[str | None] = mapped_column(String, nullable=True)
    # For Apple: stores extra data (id_token, user info) - encrypted
    extra_data_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
