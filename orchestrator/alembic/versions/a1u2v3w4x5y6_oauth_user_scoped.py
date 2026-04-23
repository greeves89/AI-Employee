"""Add user_id to oauth_integrations for per-user tokens.

Revision ID: a1u2v3w4x5y6
Revises: z0t1u2v3w4x5
Create Date: 2026-04-23

"""
from alembic import op
import sqlalchemy as sa

revision = "a1u2v3w4x5y6"
down_revision = "z0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add user_id column (nullable — NULL means global/admin token)
    op.add_column(
        "oauth_integrations",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_oauth_integrations_user_id", "oauth_integrations", ["user_id"])

    # Drop the old unique constraint on provider alone
    op.drop_constraint("oauth_integrations_provider_key", "oauth_integrations", type_="unique")

    # Two partial unique indexes replace the old single unique constraint:
    # 1) Only one global (user_id IS NULL) token per provider
    op.execute(
        "CREATE UNIQUE INDEX uq_oauth_global ON oauth_integrations (provider) WHERE user_id IS NULL"
    )
    # 2) One token per (provider, user) pair when user_id IS NOT NULL
    op.execute(
        "CREATE UNIQUE INDEX uq_oauth_user ON oauth_integrations (provider, user_id) WHERE user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_oauth_user")
    op.execute("DROP INDEX IF EXISTS uq_oauth_global")
    op.drop_index("ix_oauth_integrations_user_id", "oauth_integrations")
    op.drop_column("oauth_integrations", "user_id")
    op.create_unique_constraint("oauth_integrations_provider_key", "oauth_integrations", ["provider"])
