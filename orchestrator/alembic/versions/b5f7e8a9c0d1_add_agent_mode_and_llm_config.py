"""add agent mode and llm_config

Revision ID: b5f7e8a9c0d1
Revises: a270dc70375b
Create Date: 2026-02-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5f7e8a9c0d1'
down_revision: Union[str, None] = 'a270dc70375b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('mode', sa.String(), server_default='claude_code', nullable=False))
    op.add_column('agents', sa.Column('llm_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('agents', 'llm_config')
    op.drop_column('agents', 'mode')
