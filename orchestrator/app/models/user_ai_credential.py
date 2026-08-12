"""Der EIGENE Claude-Code- oder Codex-Zugang eines Nutzers.

Bisher gab es genau einen Zugang für die ganze Installation: eine Einstellung,
ein Token, alle Agenten. Das hatte drei Folgen, und jede davon hat wehgetan:

* **Ein Ausfall traf alle.** Alle Codex-Agenten teilen sich einen rotierenden
  Refresh-Token; erneuert ihn einer, sind die anderen tot. Genau deshalb muss das
  Neuerstellen bis heute serialisiert werden.
* **Die Kosten liefen zusammen.** Wer viel arbeitet, belastet dasselbe Konto wie
  alle anderen, und niemand kann es auseinanderrechnen.
* **Wer sein eigenes Abo hat, konnte es nicht benutzen.**

Hier liegt deshalb je Nutzer und Laufzeit **ein** Zugang, verschlüsselt. Getrennte
Zugänge heissen getrennte Token-Familien: die Rotation des einen kann die des
anderen nicht mehr umbringen.

Die Reihenfolge beim Auflösen steht in :mod:`app.core.agent_credentials` — kurz:
eigener Zugang zuerst, Teamlizenz nur, wenn der Administrator sie erlaubt.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Laufzeiten mit eigenem Anmeldeweg. Der Custom-LLM-Weg gehört NICHT dazu — dort
#: hängt der Zugang am AI-Konto, nicht an einem Abo.
CREDENTIAL_HARNESSES = ("claude_code", "codex")


class UserAiCredential(Base):
    __tablename__ = "user_ai_credentials"
    __table_args__ = (
        # Ein Zugang je Nutzer und Laufzeit. Zwei wären keine Wahlmöglichkeit,
        # sondern eine offene Frage, welcher gilt.
        UniqueConstraint("user_id", "harness", name="uq_user_ai_credential"),
        Index("ix_user_ai_credentials_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    #: "claude_code" | "codex"
    harness: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Fernet-verschlüsselt. Bei Claude Code ein OAuth-Token, bei Codex der
    #: vollständige Inhalt der ``auth.json``. Beides ist ein Zugang zu einem
    #: bezahlten Abo — im Klartext hätte es hier nichts zu suchen.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    #: Was der Nutzer sieht, damit er seinen Zugang wiedererkennt ("Claude Max",
    #: "ChatGPT Plus privat"). Nie das Geheimnis selbst.
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: ok | auth_failed | unknown — gesetzt vom letzten echten Lauf, nicht geraten.
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
