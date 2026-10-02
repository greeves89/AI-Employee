"""Blogbeitraege der Landingpage.

Die Landingpage ist statisch; ihr Blog ist — wie das Kontaktformular — eine
Funktion des Orchestrators, die nur dort laeuft, wo der Betreiber sie
einschaltet (``BLOG_ENABLED``). Ein Beitrag ist Markdown plus die Angaben, die
eine Suchmaschine braucht: Titel, Beschreibung, Hauptbegriff, Fragen mit
Antworten. Geschrieben wird ueber den MCP-Dienst (``api/blog_mcp.py``),
ausgeliefert als fertiges HTML (``api/blog_public.py``).

``status``:
  ``draft``     — nur ueber den MCP-Dienst und die Vorschau-Adresse sichtbar.
  ``published`` — oeffentlich, in Uebersicht, Feed und Sitemap.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
BLOG_STATUS = (STATUS_DRAFT, STATUS_PUBLISHED)


class BlogPost(Base, TimestampMixin):
    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Adresse: /blog/<slug>. Aendert sich nach der Veroeffentlichung nicht mehr —
    # eine umbenannte Adresse verliert ihre Verweise.
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Beschreibung fuer Suchtreffer und Vorschaukarten.
    description: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    # Hauptbegriff, auf den der Beitrag gefunden werden soll.
    keyword: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Fragen und Antworten am Ende des Beitrags: [{"frage": ..., "antwort": ...}]
    faq: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    author: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Name des Titelbilds (``blog_images.name``) — oben im Beitrag, auf den
    # Karten der Uebersicht und als Vorschaubild beim Teilen. Leer: keines.
    cover: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Wortzahl des Textes, beim Speichern gezaehlt: Uebersicht und Karten
    # brauchen sie fuer die Lesezeit und sollen dafuer nicht jeden Beitrag bei
    # jedem Aufruf neu zerlegen.
    words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Zufaelliger Schluessel der Vorschau-Adresse eines Entwurfs. Wird beim
    # Zurueckziehen neu vergeben — eine einmal weitergegebene Vorschau-Adresse
    # gilt danach nicht mehr.
    preview_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_DRAFT, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
