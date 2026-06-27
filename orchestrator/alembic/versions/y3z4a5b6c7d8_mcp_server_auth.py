"""Add mcp_servers.auth_token_encrypted (Bearer auth)

Revision ID: y3z4a5b6c7d8
Revises: x2y3z4a5b6c7
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "y3z4a5b6c7d8"
down_revision = "x2y3z4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("auth_token_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_servers", "auth_token_encrypted")
