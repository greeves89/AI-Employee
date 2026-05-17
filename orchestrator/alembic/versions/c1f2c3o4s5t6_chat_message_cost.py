"""Add cost/token columns to chat_messages for chat cost tracking.

Revision ID: c1f2c3o4s5t6
Revises: c1d2e3f4g5h6
Create Date: 2026-05-17
"""

import sqlalchemy as sa
from alembic import op

revision = "c1f2c3o4s5t6"
down_revision = "c1d2e3f4g5h6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))
        batch.add_column(sa.Column("input_tokens", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("output_tokens")
        batch.drop_column("input_tokens")
        batch.drop_column("cost_usd")
