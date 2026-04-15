"""add claude_md to agent_templates

Revision ID: b1b4f9d7981f
Revises: f2da25b5ed7d
Create Date: 2026-04-08 10:50:31.045041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1b4f9d7981f'
down_revision: Union[str, None] = 'f2da25b5ed7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent_templates', sa.Column('claude_md', sa.Text(), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('agent_templates', 'claude_md')
