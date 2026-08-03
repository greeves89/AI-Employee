"""External MCP server registry."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class McpServer(Base, TimestampMixin):
    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    tools: Mapped[dict] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional Bearer token (Fernet-encrypted) sent as `Authorization: Bearer <token>`
    # on discovery and on every agent tool call to this server.
    auth_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional custom auth headers (Fernet-encrypted JSON {header: value}) merged over
    # the base headers — for servers that expect a non-Bearer key (x-api-key,
    # x-consumer-api-key, X-Auth-Token, …). The bearer_token above stays as a shortcut.
    headers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Client-side OAuth (#426) -------------------------------------------
    # Set once the server is discovered to be OAuth-protected (RFC 9728 → 8414).
    # The live access token lives in `auth_token_encrypted` above so it flows to
    # agents unchanged; these columns hold what's needed to keep it fresh.
    oauth_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    oauth_authorization_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_registration_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RFC 8707 resource indicator — the protected resource id from the PRM doc.
    oauth_resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Fernet-encrypted; only set for confidential (non-PKCE-public) DCR clients.
    oauth_client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-encrypted refresh token (rotated on use).
    oauth_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Absolute expiry of the access token currently in auth_token_encrypted.
    oauth_access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
