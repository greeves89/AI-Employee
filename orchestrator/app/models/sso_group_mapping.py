"""Zuordnung von IdP-Gruppen (Entra/Azure AD, SAML) auf eine Rolle dieser Plattform.

Loest ``saml_group_role_map`` (freies JSON in den Einstellungen, nur SAML, nur die
drei Enum-Rollen) ab: EINE Tabelle fuer beide Anmeldewege (SAML *und* der normale
Microsoft-OIDC-Login, der bisher gar keine Gruppen las), und das Ziel kann jetzt auch
eine ``CustomRole`` sein — nicht nur admin/manager/member. Ein zweiter Mechanismus
fuer dieselbe Aufgabe war genau die Stelle, an der Rechte auseinandergelaufen waeren.

``priority``: trifft mehr als eine Gruppe zu, gewinnt die mit der hoechsten Zahl.
Explizit statt einer impliziten Rangfolge (frueher: admin > manager > member fest
verdrahtet) — das trug nicht mehr, sobald das Ziel auch eine CustomRole sein kann,
zwischen denen es keine natuerliche Rangfolge gibt.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TARGET_KIND_ROLE = "role"
TARGET_KIND_CUSTOM_ROLE = "custom_role"
TARGET_KINDS = (TARGET_KIND_ROLE, TARGET_KIND_CUSTOM_ROLE)

PROVIDERS = ("saml", "microsoft")


class SsoGroupRoleMapping(Base):
    __tablename__ = "sso_group_role_mappings"
    __table_args__ = (
        # Eine Gruppe pro Anbieter zeigt auf genau ein Ziel — sonst waere nicht
        # definiert, welches der beiden gilt.
        UniqueConstraint("provider", "group_name", name="ux_sso_group_role_provider_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # "admin"|"manager"|"member" bei target_kind="role", sonst die CustomRole-ID als Text.
    target_value: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
