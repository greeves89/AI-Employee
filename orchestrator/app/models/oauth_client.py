"""Registered OAuth 2.1 clients for the built-in MCP authorization server.

One row per external client (e.g. an OpenWebUI instance) that registered via
RFC 7591 Dynamic Client Registration against ``/api/v1/oauth/register``. Public
clients (PKCE, ``token_endpoint_auth_method=none``) have no secret; confidential
clients store a bcrypt hash of their secret.
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OAuthClient(Base, TimestampMixin):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # bcrypt hash for confidential clients; NULL for public (PKCE) clients.
    client_secret_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON-encoded list of allowed redirect URIs (exact match enforced on use).
    redirect_uris: Mapped[str] = mapped_column(Text, default="[]")
    grant_types: Mapped[str] = mapped_column(String(255), default="authorization_code refresh_token")
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(32), default="none")
    scope: Mapped[str] = mapped_column(Text, default="")
