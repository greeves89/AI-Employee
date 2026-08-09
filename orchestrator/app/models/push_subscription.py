"""Web-Push-Anmeldungen — das Browser-Gegenstueck zu ``device_tokens`` (APNs).

Ein Browser meldet sich mit drei Angaben an: der Endpunkt-Adresse seines
Push-Dienstes und zwei Schluesseln (``p256dh`` und ``auth``), mit denen der Inhalt
fuer genau ihn verschluesselt wird. Ohne die beiden koennte der Push-Dienst mitlesen.

Ein Nutzer hat typischerweise mehrere Eintraege — je Browser und Geraet einen.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    # Die Adresse beim Push-Dienst (FCM/Mozilla/Apple). Eindeutig — meldet sich
    # derselbe Browser erneut an, wird der bestehende Eintrag aufgefrischt statt
    # ein zweiter angelegt, sonst kaeme jede Meldung doppelt an.
    endpoint: Mapped[str] = mapped_column(Text, unique=True, index=True)
    p256dh: Mapped[str] = mapped_column(String)
    auth: Mapped[str] = mapped_column(String)
    # Nur zur Wiedererkennung in der Oberflaeche ("Chrome auf dem Laptop").
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
