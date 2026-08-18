import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"
    VIEWER = "viewer"
    # Angemeldet, aber noch nichts zugeteilt. Bewusst eine eigene Rolle und kein
    # zweiter Wahrheitswert neben ``approved``: wer sich selbst registriert, ist
    # eine echte Person mit einem echten Konto — er hat nur noch keine Aufgabe.
    #
    # Der Anlass: wer sich über Microsoft anmeldet, um dem M365-MCP zuzustimmen,
    # bekam als Nebenwirkung die volle Oberfläche mit fünf Agenten — obwohl ihm
    # niemand etwas zugewiesen hatte. Zustimmen und Mitarbeiten waren dieselbe
    # Berechtigung.
    #
    # WICHTIG: Diese Rolle sperrt die Oberfläche, NICHT den MCP-Zugang. Der läuft
    # über ``approved``/``is_active`` und bleibt absichtlich offen — genau darum
    # geht es: Postfach ja, Plattform nein.
    UNASSIGNED = "unassigned"


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("sso_provider", "sso_subject", name="uq_sso_identity"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.MEMBER)
    # Optional override — if set, custom_role.permissions wins over the role enum defaults
    custom_role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("custom_roles.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Einzelfreigabe fuer ein EIGENES Claude-/Codex-Abo. Greift nur, wenn der
    #: globale Schalter aus ist — genau die zweite Ebene, die der Kunde am
    #: 18.08.2026 verlangt hat: „generell unterbinden … fuer User manuell
    #: freischalten".
    allow_personal_credentials: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Admin-approval gate (OpenWebUI-style). True = approved/usable. New self-registered
    # users (SSO or password) get False when `require_user_approval` is on → they wait
    # for an admin to approve. Distinct from is_active (deactivation). Default True so
    # existing users and admin-created users are unaffected.
    approved: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sso_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    sso_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    # Activity tracking for lifecycle management (auto-stop/start user's agents)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Monthly spend cap across ALL of the user's agents (None = unlimited).
    # When exceeded, every agent of this user behaves per its budget_exceeded_action.
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
