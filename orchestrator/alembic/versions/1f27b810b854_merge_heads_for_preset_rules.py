"""merge_heads_for_preset_rules

Revision ID: 1f27b810b854
Revises: c1d2e3f4a5b6, u5o6p7q8r9s0, u5o6p7q8r9s0b
Create Date: 2026-04-23 20:16:49.970419

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f27b810b854'
down_revision: Union[str, None] = ('c1d2e3f4a5b6', 'u5o6p7q8r9s0', 'u5o6p7q8r9s0b')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
