"""App-Freigaben (#467): wer darf eine von einem Agenten deployte App öffnen?

Eine "App" ist ein docker-compose-Projekt aus dem Agenten-Workspace und wird
plattformweit über seinen **Projektnamen** (``agent-{agentId8}-{pfad}``, siehe
``docker_apps._project_name``) identifiziert — derselbe Schlüssel, den die
Apps-Übersicht schon benutzt. Genau darauf hängt die Freigabe.

Default ist und bleibt **deny**: ohne Eintrag hier kommt nur der Besitzer des
Agenten (bzw. Admin/AgentAccess) an die App. Drei Stufen:

* ``user``           — namentlich freigegeben, Login nötig
* ``authenticated``  — alle eingeloggten Plattform-Nutzer
* ``public``         — Link mit Token, **ohne** Login (Ablaufdatum Pflicht)

Wichtig: Eine Freigabe öffnet ausschließlich den *Zugriffsweg* auf den Proxy.
Die beiden SSRF-Gates im Proxy (Container-Projekt-Präfix + compose-Label)
bleiben davon unberührt — geteilt wird nie ein anderes Ziel, nur dieselbe App.
Steuernde Aktionen (Start/Stop/Entfernen/Rebuild/Logs) bleiben Besitzer-only.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Erlaubte Freigabe-Stufen (aufsteigende Reichweite).
APP_SHARE_SCOPES = ("user", "authenticated", "public")


def hash_share_token(token: str) -> str:
    """SHA-256 des Link-Tokens. Kein Passwort-KDF nötig und auch nicht sinnvoll:
    der Token ist 256 Bit aus ``secrets.token_urlsafe`` — nicht erratbar, aber
    bei jedem Seitenaufruf zu prüfen, also muss es schnell sein."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AppShare(Base):
    __tablename__ = "app_shares"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    #: compose-Projektname der App — der plattformweite App-Schlüssel.
    project: Mapped[str] = mapped_column(String, index=True, nullable=False)
    #: Besitzender Agent (für Revoke-Prüfung + Auflistung ohne Docker-Zugriff).
    agent_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    #: Nur bei scope="user": der/die Beschenkte.
    user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    #: Nur bei scope="public": SHA-256 des Link-Tokens, NIE der Token selbst.
    #: Der Klartext existiert genau einmal — in der Antwort auf das Anlegen. Ein
    #: Datenbank-Leak oder ein altes Backup gibt damit keine gültigen Links her.
    token_hash: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    #: Bei scope="public" Pflicht, sonst optional.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc)
        exp = self.expires_at
        # Aus SQLite/alten Zeilen kann ein naives datetime kommen — als UTC lesen,
        # sonst wirft der Vergleich TypeError und die Freigabe fällt nie ab.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= now
