"""Graph-Abonnement eines Agenten (Plan docs/plaene/agenten-identitaet-m365.md, Phase 2).

Ein Agent mit eigenem Microsoft-Konto (core/agenten_konto) lässt sich bei neuer Mail wecken.
Dafür abonniert die Anlage bei Microsoft Graph den Posteingang des Agenten. Microsoft meldet
dann neue Nachrichten an ``/api/v1/msgraph/benachrichtigung`` (core/graph_abos).

Ohne öffentlich erreichbare HTTPS-Adresse gibt es kein Abonnement bei Microsoft
(``subscription_id`` leer): Dann fragt die Anlage regelmäßig nach (Mails seit ``stand``).
Das Geheimnis für ``clientState`` wird nicht gespeichert, sondern aus ``ENCRYPTION_KEY``
und der Kennung abgeleitet.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GraphAbo(Base, TimestampMixin):
    __tablename__ = "graph_abos"
    __table_args__ = (UniqueConstraint("agent_id", "art", name="uq_graph_abo_agent_art"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    #: ``mail`` (Posteingang). Teams-Chats folgen mit Phase 3 (eigene Teams-Kennung).
    art: Mapped[str] = mapped_column(String(16))
    ressource: Mapped[str] = mapped_column(String(200))
    #: Kennung bei Microsoft; leer = Abfrage-Betrieb (keine öffentliche Adresse).
    subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    #: Ablauf bei Microsoft; im Abfrage-Betrieb: nächster Versuch, das Abo anzulegen.
    ablauf: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Bis wann nachgesehen wurde (ISO-Zeit, UTC) — Abfrage-Betrieb, stündliches Netz, ``missed``.
    stand: Mapped[str | None] = mapped_column(String(40), nullable=True)
    letzte_meldung: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fehler: Mapped[str | None] = mapped_column(String(300), nullable=True)
