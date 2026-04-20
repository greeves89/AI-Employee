"""Add input_tokens and output_tokens to tasks table

Revision ID: y9s0t1u2v3w4
Revises: x8r9s0t1u2v3
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa


revision = "y9s0t1u2v3w4"
down_revision = "x8r9s0t1u2v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("output_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "output_tokens")
    op.drop_column("tasks", "input_tokens")
