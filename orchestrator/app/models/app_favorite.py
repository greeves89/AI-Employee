"""App-Favoriten: welche Apps hat ein Nutzer angepinnt?

Wie bei den Freigaben (``app_share.py``) ist der Schlüssel der **compose-
Projektname** (``agent-{agentId8}-{pfad}``, siehe ``docker_apps._project_name``)
— derselbe, den die Apps-Übersicht ohnehin benutzt.

Bewusst eine eigene Tabelle statt eines Feldes am Nutzer: Ein Nutzer hat
beliebig viele Favoriten, und ein Favorit gehört zu genau einem Nutzer. Eine
Liste in einer JSON-Spalte wäre beim Setzen und Entfernen ein Lesen-Ändern-
Schreiben mit Wettlauf; hier ist es ein Einfügen oder ein Löschen.

Nicht zu verwechseln mit ``Agent.favorite``: Das ist „höchstens einer pro
Nutzer" und steuert das iOS-Startdashboard. Hier geht es um Anpinnen im
üblichen Sinn — beliebig viele, nur zur Sortierung.

**Mandantentrennung:** Ein Favoriteneintrag verleiht KEINEN Zugriff. Er merkt
sich nur eine Vorliebe. Ob jemand eine App sehen darf, entscheidet
weiterhin allein ``_visible_agents()`` plus ``shared_projects_for_user()`` in
``api/apps_overview.py``. Ein Favorit auf eine App, die einem nicht mehr
gezeigt wird, ist wirkungslos — er taucht schlicht nirgends auf.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppFavorite(Base):
    __tablename__ = "app_favorites"
    __table_args__ = (
        # Zweimal dasselbe anpinnen ergibt keinen Sinn — und die Eindeutigkeit
        # macht das Setzen idempotent, statt Dubletten anzuhäufen.
        UniqueConstraint("user_id", "project", name="uq_app_favorite_user_project"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Wer hat angepinnt.
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    #: compose-Projektname der App — der plattformweite App-Schlüssel.
    project: Mapped[str] = mapped_column(String, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
