"""add anthropic oauth provider enum value

Revision ID: g1a2b3c4d5e6
Revises: f0c84741c692
Create Date: 2026-03-18
"""

from alembic import op

# revision identifiers
revision = "g1a2b3c4d5e6"
down_revision = "f0c84741c692"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'anthropic' to the oauthprovider enum type
    op.execute("ALTER TYPE oauthprovider ADD VALUE IF NOT EXISTS 'anthropic'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values easily
    pass
