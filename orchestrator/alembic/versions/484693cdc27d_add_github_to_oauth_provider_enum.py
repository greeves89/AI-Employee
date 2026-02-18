"""add github to oauth_provider enum

Revision ID: 484693cdc27d
Revises: 2992c8efc2ad
Create Date: 2026-02-17 07:33:28.638487

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '484693cdc27d'
down_revision: Union[str, None] = '2992c8efc2ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLAlchemy Enum() uses Python enum NAMES (uppercase) as DB values
    op.execute("ALTER TYPE oauthprovider ADD VALUE IF NOT EXISTS 'GITHUB'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from enums easily.
    # This would require recreating the type + column. Skipping for safety.
    pass
