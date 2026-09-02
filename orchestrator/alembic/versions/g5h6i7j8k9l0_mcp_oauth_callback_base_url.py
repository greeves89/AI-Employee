"""Add per-MCP OAuth callback base URL

Revision ID: g5h6i7j8k9l0
Revises: f4a1c9d2e6b7
Create Date: 2026-08-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g5h6i7j8k9l0"
down_revision: Union[str, None] = "f4a1c9d2e6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("oauth_callback_base_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_servers", "oauth_callback_base_url")
