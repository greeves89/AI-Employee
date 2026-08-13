"""Eigene Menuepunkte: fremde Seiten als eingebetteter Rahmen oder als Link.

Der Anlass war OpenWebUI beim Kunden — die Oberflaeche soll nicht "daneben"
stehen, sondern im selben Menue erreichbar sein wie alles andere. Statt dafuer
einen Sonderfall zu bauen, ist es hier allgemein: ein Administrator legt eine
Seite an, sie erscheint als Menuepunkt unter ``/p/<slug>``, und die vorhandene
Rechtevergabe (``permissions.menu_paths``) entscheidet, wer sie sieht. Also
KEINE zweite Rechte-Logik neben der bestehenden.

``open_mode``:
  ``iframe`` — die Seite wird in unserer Oberflaeche eingebettet.
  ``link``   — der Menuepunkt oeffnet die URL in einem neuen Tab.

Ob sich eine fremde Seite ueberhaupt einbetten laesst, entscheidet ausschliesslich
diese Seite selbst (``X-Frame-Options`` / ``Content-Security-Policy:
frame-ancestors``). Wir koennen das weder erzwingen noch vorher erkennen — die
Oberflaeche weist deshalb darauf hin und bietet den Weg im neuen Tab an.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Menuegruppen der Seitenleiste. Muss zu ``navGroups`` in
# frontend/src/components/layout/sidebar.tsx passen — eine unbekannte Gruppe
# wuerde den Menuepunkt verschlucken, deshalb wird der Wert beim Anlegen geprueft.
GROUP_KEYS = ("overview", "collab", "automation", "system", "help")

OPEN_MODES = ("iframe", "link")


class CustomPage(Base):
    __tablename__ = "custom_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Adresse im Menue: /p/<slug>. Eindeutig, weil daraus der Rechte-Pfad wird.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Text statt String(n): manche Portale haengen lange Parameter an.
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # Name eines lucide-Symbols; das Frontend faellt auf ein Standardsymbol zurueck.
    icon: Mapped[str] = mapped_column(String(60), nullable=False, default="Globe")
    group_key: Mapped[str] = mapped_column(String(20), nullable=False, default="collab")
    open_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="iframe")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Mikrofon/Kamera an die eingebettete Seite durchreichen — nur wenn der
    # Administrator es fuer diese eine Adresse bewusst erlaubt.
    allow_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @property
    def menu_path(self) -> str:
        return f"/p/{self.slug}"
