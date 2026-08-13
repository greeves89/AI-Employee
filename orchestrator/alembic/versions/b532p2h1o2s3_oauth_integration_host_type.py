"""oauth_integrations: add host_type + base_url (#532 phase 2)

Lets a Git-host integration describe a self-hosted instance (GitHub
Enterprise Server today, Forgejo/Gitea in a later phase) instead of the host
being implied by the fixed OAuthProvider enum value. Both columns are
nullable and unused by existing rows, so current behaviour (public GitHub,
public Google/Microsoft/...) is unchanged.

Revision ID: b532p2h1o2s3
Revises: v7h1b2r3d4s5
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "b532p2h1o2s3"
down_revision = "v7h1b2r3d4s5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_integrations",
        sa.Column("host_type", sa.String(), nullable=True),
    )
    op.add_column(
        "oauth_integrations",
        sa.Column("base_url", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_integrations", "base_url")
    op.drop_column("oauth_integrations", "host_type")
