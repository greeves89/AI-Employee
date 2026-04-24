"""Add user_id to oauth_integrations for per-user tokens.

Revision ID: a2u3v4w5x6y7
Revises: e286ff01d6fc
Create Date: 2026-04-23

"""
from alembic import op
import sqlalchemy as sa

revision = "a2u3v4w5x6y7"
down_revision = "e286ff01d6fc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_integrations",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_oauth_integrations_user_id", "oauth_integrations", ["user_id"])
    op.drop_constraint("oauth_integrations_provider_key", "oauth_integrations", type_="unique")
    op.execute(
        "CREATE UNIQUE INDEX uq_oauth_global ON oauth_integrations (provider) WHERE user_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_oauth_user ON oauth_integrations (provider, user_id) WHERE user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_oauth_user")
    op.execute("DROP INDEX IF EXISTS uq_oauth_global")
    op.drop_index("ix_oauth_integrations_user_id", "oauth_integrations")
    op.drop_column("oauth_integrations", "user_id")
    op.create_unique_constraint("oauth_integrations_provider_key", "oauth_integrations", ["provider"])
