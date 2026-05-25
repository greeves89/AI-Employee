"""add codex oauth provider enum value

Revision ID: h1i2j3k4l5m6
Revises: apns1dev2token3
Create Date: 2026-05-24
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "h1i2j3k4l5m6"
down_revision = "apns1dev2token3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy Enum() stores Python enum names in PostgreSQL.
    op.execute("ALTER TYPE oauthprovider ADD VALUE IF NOT EXISTS 'CODEX'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    pass
