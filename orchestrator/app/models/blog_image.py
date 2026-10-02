"""Bilder des Blogs.

Titelbilder, Grafiken und Bildschirmfotos zu den Beitraegen. Sie liegen in der
Datenbank und nicht im Dateisystem, damit sie mit jeder Sicherung der
Datenbank mitgehen und ein neu gebauter Container nichts verliert. Erlaubt sind
PNG, JPEG und WebP bis zu einer festen Groesse (``core/blog.py``) — kein SVG,
weil ein SVG Skripte tragen kann.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.models.base import Base


class BlogImage(Base):
    __tablename__ = "blog_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Dateiname in der Adresse: /blog/media/<name>
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Beschreibung fuer Screenreader und Suchmaschinen; Vorgabe fuer den alt-Text.
    alt: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Pruefsumme des Inhalts: Browser und Zwischenspeicher fragen damit nach,
    # ob sich das Bild geaendert hat, ohne dass die Daten geladen werden.
    etag: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    # Die Bilddaten werden nur geladen, wenn das Bild ausgeliefert wird.
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
