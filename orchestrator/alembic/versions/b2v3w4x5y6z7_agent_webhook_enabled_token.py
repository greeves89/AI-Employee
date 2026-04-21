"""add webhook_enabled and webhook_token to agents

Revision ID: b2v3w4x5y6z7
Revises: a1u2v3w4x5y6
Create Date: 2026-04-21

"""
from alembic import op
import sqlalchemy as sa

revision = "b2v3w4x5y6z7"
down_revision = "a1u2v3w4x5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("webhook_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("agents", sa.Column("webhook_token", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "webhook_token")
    op.drop_column("agents", "webhook_enabled")
