"""Pydantic schemas for OAuth integrations."""

from datetime import datetime

from pydantic import BaseModel


class IntegrationStatus(BaseModel):
    provider: str
    display_name: str
    icon: str
    description: str
    connected: bool
    # connected | refresh_failing | expired | disconnected — siehe
    # app/core/integration_health.py. "connected" allein sagt nur, dass eine
    # Zeile existiert; ob der Token noch taugt, steht hier.
    status: str = "disconnected"
    refresh_error: str | None = None  # letzter Refresh-Fehler, ohne Token-Inhalte
    account_label: str | None = None
    expires_at: datetime | None = None
    scopes: str = ""
    available: bool  # True if client credentials are configured
    auth_type: str = "oauth"  # "oauth" or "pat"
    per_user: bool = False  # True if each user needs their own OAuth login


class IntegrationListResponse(BaseModel):
    integrations: list[IntegrationStatus]


class AuthUrlResponse(BaseModel):
    auth_url: str
    provider: str


class AgentIntegrationsUpdate(BaseModel):
    integrations: list[str]


class AgentIntegrationsResponse(BaseModel):
    agent_id: str
    integrations: list[str]
