from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: Freigabe nur zum Nutzen: chatten, sprechen, Dateien schicken und abholen.
#: Einstellungen, Gedaechtnis, Zeitplaene, Auswertungen und Loeschen bleiben beim
#: Besitzer und beim Admin (core/ownership, dependencies.require_agent_access).
ROLLE_NUTZEN = "nutzen"


class AgentAccess(Base, TimestampMixin):
    """Grants a user access to a specific agent (used for Member/Viewer roles)."""

    __tablename__ = "agent_access"

    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    granted_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    #: ``ROLLE_NUTZEN`` oder None. None = aeltere Eintraege (Zuweisen, Verteilen),
    #: dort ist der Nutzer ohnehin Besitzer — sie behalten ihre bisherige Wirkung.
    rolle: Mapped[str | None] = mapped_column(String(16), nullable=True)
