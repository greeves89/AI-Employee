"""Key Management System — encrypted API keys, SSO profiles, OAuth tokens.

Secrets are stored Fernet-encrypted. At task start they are injected as env vars
into the agent container using the key_name field (e.g. AZURE_AI_SEARCH_KEY).
"""

from enum import Enum

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class SecretType(str, Enum):
    API_KEY = "api_key"
    SSO_PROFILE = "sso_profile"
    OAUTH_TOKEN = "oauth_token"


class AgentSecret(Base, TimestampMixin):
    __tablename__ = "agent_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    key_name: Mapped[str] = mapped_column(String(100))
    value_encrypted: Mapped[str] = mapped_column(Text)
    secret_type: Mapped[str] = mapped_column(String(30), default=SecretType.API_KEY)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Wem das Secret gehoert (Nutzer-ID). NULL = Firmen-Secret eines Admins,
    # das Mitglieder nur ueber die Rollen-Freigabe (secret_ids) erreichen.
    owner_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    assignments: Mapped[list["AgentSecretAssignment"]] = relationship(
        back_populates="secret", cascade="all, delete-orphan", lazy="selectin"
    )


class AgentSecretAssignment(Base, TimestampMixin):
    __tablename__ = "agent_secret_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    secret_id: Mapped[int] = mapped_column(ForeignKey("agent_secrets.id", ondelete="CASCADE"))

    secret: Mapped["AgentSecret"] = relationship(back_populates="assignments")


class AgentSecretShare(Base, TimestampMixin):
    """Freigabe eines Keys an eine bestimmte Person (core/secret_zugriff.py).

    Der Besitzer — oder ein Admin — gibt einen Key an einzelne Nutzer frei; die
    duerfen ihn ihren eigenen Agenten zuweisen, sehen den Wert aber nie und koennen
    ihn weder aendern noch weitergeben. An Rollen gibt nur ein Admin frei, und zwar
    ueber die Rollenrechte (``secret_ids``), nicht hier.
    """
    __tablename__ = "agent_secret_shares"
    __table_args__ = (UniqueConstraint("secret_id", "user_id", name="uq_agent_secret_shares_secret_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    secret_id: Mapped[int] = mapped_column(ForeignKey("agent_secrets.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
