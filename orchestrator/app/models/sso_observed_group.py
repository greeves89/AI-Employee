"""Gruppennamen, die beim SSO-Login tatsaechlich vom Anbieter kamen.

Der alte Weg (rohe JSON-Textbox mit Gruppen-zu-Rolle-Zuordnung) verlangte, den
exakten Gruppennamen aus Entra/ADFS von Hand abzutippen — ein Vertipper zeigte sich
erst beim naechsten Login eines Betroffenen, nicht beim Speichern. Diese Tabelle
haelt fest, welche Gruppen tatsaechlich gesehen wurden, damit die Verwaltung sie zum
Anklicken statt zum Abtippen anbieten kann.

Reine Beobachtung, keine Berechtigung: ein Eintrag hier oeffnet nichts, das macht
ausschliesslich ``SsoGroupRoleMapping``.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SsoObservedGroup(Base):
    __tablename__ = "sso_observed_groups"
    __table_args__ = (
        UniqueConstraint("provider", "group_name", name="ux_sso_observed_provider_group"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(String(200), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
