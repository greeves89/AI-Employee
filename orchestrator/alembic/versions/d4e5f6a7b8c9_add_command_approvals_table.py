"""add command_approvals table

Revision ID: d4e5f6a7b8c9
Revises: c3fc5773d515
Create Date: 2026-02-18 15:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3fc5773d515'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create command_approvals table
    op.create_table(
        'command_approvals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=True),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('risk_level', sa.String(), nullable=False, server_default='medium'),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('user_response', sa.String(), nullable=True),
        sa.Column('meta', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('ix_command_approvals_agent_id', 'command_approvals', ['agent_id'])
    op.create_index('ix_command_approvals_task_id', 'command_approvals', ['task_id'])
    op.create_index('ix_command_approvals_status', 'command_approvals', ['status'])
    op.create_index('ix_command_approvals_created_at', 'command_approvals', ['created_at'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_command_approvals_created_at', table_name='command_approvals')
    op.drop_index('ix_command_approvals_status', table_name='command_approvals')
    op.drop_index('ix_command_approvals_task_id', table_name='command_approvals')
    op.drop_index('ix_command_approvals_agent_id', table_name='command_approvals')

    # Drop table
    op.drop_table('command_approvals')
