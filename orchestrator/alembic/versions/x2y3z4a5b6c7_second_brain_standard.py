"""Add second_brains.standard (vault formatting standard)

Revision ID: x2y3z4a5b6c7
Revises: w1x2y3z4a5b6
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "x2y3z4a5b6c7"
down_revision = "w1x2y3z4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "second_brains",
        sa.Column("standard", sa.String(), nullable=False, server_default="freeform"),
    )


def downgrade() -> None:
    op.drop_column("second_brains", "standard")
